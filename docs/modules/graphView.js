// docs/modules/graphView.js

import * as PIXI from 'https://cdn.jsdelivr.net/npm/pixi.js@8.13.2/dist/pixi.mjs';
import CONFIG from './config.js';
import { rgbToHex, easeInOutQuad, debounce } from './utils.js';
import { t, getCurrentLanguage } from './i18n.js';

// 用于控制异步渲染，每一帧最多处理（创建或销毁）多少个图形对象
const ASYNC_CONFIG = {
    ITEMS_PER_FRAME: 50, 
};

export class GraphView {
    constructor(selector, callbacks) {
        this.containerEl = document.querySelector(selector);
        this.width = this.containerEl.getBoundingClientRect().width;
        this.height = this.containerEl.getBoundingClientRect().height;
        this.tooltip = document.querySelector(".tooltip");
        this.callbacks = callbacks;
        
        this.colorScale = d3.scaleOrdinal();
        // 标志位，用于区分首次加载
        this.isInitialRender = true;
        this.animationSourceNode = null;
        this.initialQuadrant = Math.floor(Math.random() * 4);
        // 存储当前高亮节点及其邻居信息
        this.highlightedNodeId = null;
        this.highlightedNeighbors = {};

        // 定义所有视觉状态的样式
        this.styleStates = {
            NODE_DEFAULT: {
                alpha: 1, 
                eventMode: 'static'
            },
            NODE_FADED: {
                alpha: 0.1,
                eventMode: 'none'
            },
            LINK_DEFAULT: {
                alpha: 1,
                tint: 0x888888,
                labelTint: 0x888888
            },
            LINK_HIGHLIGHT: {
                alpha: 1,
                labelTint: 0xFFFFFF
            }, // 高亮时，标签为白色，动态计算关系线颜色
            LINK_FADED: {
                alpha: 0.05,
                eventMode: 'none',
                tint: 0x888888,
                labelTint: 0x888888
            }
        };

        this.nodeObjects = new Map();
        this.linkObjects = new Map();

        // 异步处理任务队列
        this.creationQueue = [];
        this.removalQueue = [];
        this.isProcessingQueues = false;

        this.interactionState = {
            target: null, isDragging: false, isDown: false,
            hasMoved: false, clickTimeout: null
        };

        this.camera = {
            dragging: false, lastX: 0, lastY: 0, animation: null,
        };

        this.pathAnimationTimers = [];

        d3.select(".legend-toggle").on("click", () => {
            document.getElementById('legend-container').classList.toggle("collapsed");
        });
    }

    async init() {
        this.app = new PIXI.Application();
        await this.app.init({
            width: this.width, height: this.height, backgroundColor: 0x121212,
            antialias: true, resolution: window.devicePixelRatio || 1, autoDensity: true,
        });
        this.containerEl.appendChild(this.app.canvas);

        this.world = new PIXI.Container();
        this.app.stage.addChild(this.world);
        
        this.linkLayer = new PIXI.Container();
        this.nodeLayer = new PIXI.Container();
        this.linkLabelLayer = new PIXI.Container();
        this.nodeLabelLayer = new PIXI.Container();
        this.world.addChild(this.linkLayer, this.linkLabelLayer, this.nodeLayer, this.nodeLabelLayer);

        this.simulation = this._createSimulation();
        this.simulation.on("tick", this._handleTick.bind(this));

        this._initCameraControls();
        this.app.ticker.add(() => this._updateCameraAnimation());
        
        // 创建一个被防抖函数包裹的 resize 处理器
        const debouncedResize = debounce(this._handleResize.bind(this), 50);

        // 创建一个 ResizeObserver 实例，它的回调函数会调用处理器
        const resizeObserver = new ResizeObserver(() => {
            // 每当容器尺寸变化时，调用防抖后的resize处理器
            debouncedResize();
        });

        // 让 ResizeObserver 监视图谱容器元素
        resizeObserver.observe(this.containerEl);

        // 监听虚拟视口的变化，专门用于捕捉移动端键盘弹出/收起等事件
        if (window.visualViewport) {
            window.visualViewport.addEventListener('resize', debouncedResize);
        }
    }

    _handleResize() {
        const newWidth = this.containerEl.getBoundingClientRect().width;
        const newHeight = this.containerEl.getBoundingClientRect().height;
        this.width = newWidth;
        this.height = newHeight;
        this.app.renderer.resize(newWidth, newHeight);
        this.simulation.force("x").x(newWidth / 2);
        this.simulation.force("y").y(newHeight / 2);
        this.simulation.alpha(0.3).restart();
    }

    render(graphData) {
        // 如果是首次渲染，走同步创建流程以保证动画完整性
        if (this.isInitialRender) {
            this._renderSync(graphData);
            this.isInitialRender = false; // 完成后关闭标志
        }
        else {
            // 后续所有更新都走异步流程
            this._renderAsync(graphData);
        }
    }

    // 同步渲染函数，用于首次加载
    _renderSync(graphData) {
        const { visibleNodes, validRels } = graphData;
        
        validRels.forEach(link => this._createLinkObject(link));
        visibleNodes.forEach(node => this._createNodeObject(node));
        
        this._updateSimulation(graphData);
    }

    // 异步渲染函数，用于后续所有交互
    _renderAsync(graphData) {
        const { visibleNodes, validRels } = graphData;

        const visibleNodeIds = new Set(visibleNodes.map(n => n.id));
        const visibleLinkIds = new Set(validRels.map(l => this._getLinkId(l)));

        this.nodeObjects.forEach((obj, id) => {
            if (!visibleNodeIds.has(id)) {
                this.removalQueue.push({ type: 'node', id });
            }
        });
        this.linkObjects.forEach((obj, id) => {
            if (!visibleLinkIds.has(id)) {
                this.removalQueue.push({ type: 'link', id });
            }
        });

        // 按加载顺序将创建任务加入队列
        validRels.forEach(link => {
            if (!this.linkObjects.has(this._getLinkId(link))) {
                this.creationQueue.push({ type: 'link_line', data: link });
                this.creationQueue.push({ type: 'link_label', data: link });
            }
        });
        visibleNodes.forEach(node => {
            if (!this.nodeObjects.has(node.id)) {
                this.creationQueue.push({ type: 'node_gfx', data: node });
                this.creationQueue.push({ type: 'node_label', data: node });
            }
            else {
                // 对于已存在的节点，更新其视觉属性
                this._updateNodeObject(node);
            }
        });

        this._startQueueProcessing();
        this._updateSimulation(graphData);
    }

    _startQueueProcessing() {
        if (this.isProcessingQueues) return;
        this.isProcessingQueues = true;
        requestAnimationFrame(() => this._processQueues());
    }

    _processQueues() {
        // 1. 优先处理卸载任务
        for (let i = 0; i < ASYNC_CONFIG.ITEMS_PER_FRAME && this.removalQueue.length > 0; i++) {
            const task = this.removalQueue.shift();
            if (task.type === 'node') {
                this._removeNodeObject(task.id, true);
            }
            else {
                this._removeLinkObject(task.id, true);
            }
        }

        // 2. 处理加载任务
        for (let i = 0; i < ASYNC_CONFIG.ITEMS_PER_FRAME && this.creationQueue.length > 0; i++) {
            const task = this.creationQueue.shift();
            
            // 确保底层对象已创建
            if (task.type.startsWith('link')) {
                this._createLinkObject(task.data);
            }
            else if (task.type.startsWith('node')) {
                this._createNodeObject(task.data);
            }

            // // 根据任务类型，只显示对应的部分
            // if (task.type === 'link_line') {
            //     this.linkObjects.get(this._getLinkId(task.data)).gfx.alpha = 1;
            // } else if (task.type === 'link_label') {
            //     this.linkObjects.get(this._getLinkId(task.data)).label.alpha = 1;
            // } else if (task.type === 'node_gfx') {
            //     this.nodeObjects.get(task.data.id).gfx.alpha = 1;
            // } else if (task.type === 'node_label') {
            //     this.nodeObjects.get(task.data.id).label.alpha = 1;
            // }
        }

        if (this.creationQueue.length > 0 || this.removalQueue.length > 0) {
            requestAnimationFrame(() => this._processQueues());
        }
        else {
            this.isProcessingQueues = false;
        }
    }

    _getLinkId(link) { return `${link.source.id}-${link.target.id}-${link.type}`; }

    // 辅助函数，用于安全地获取多语言文本
    _getLocalizedText(dataObject, field) {
        if (!dataObject) return t('tooltip_na');

        // 根据字段确定要访问的数据源 ('name' 或 'properties[field]')
        const sourceObject = (field === 'name') ? dataObject.name : dataObject.properties?.[field];

        // 如果源对象不存在或不是一个对象，则直接返回最终回退值
        if (!sourceObject || typeof sourceObject !== 'object') {
            return (field === 'name') ? dataObject.id : t('tooltip_na');
        }

        const currentLang = getCurrentLanguage();

        // 1. 定义一个有优先级的语言搜索顺序
        // 使用 Set 自动处理重复项 (例如当 currentLang 就是 'zh-cn' 或 'en' 时)
        const prioritizedLangs = [...new Set([currentLang, 'zh-cn', 'en'])];

        // 2. 按优先级顺序搜索
        for (const lang of prioritizedLangs) {
            const value = sourceObject[lang];
            // 检查内容是否有效且非空
            if (field === 'name' && Array.isArray(value) && value.length > 0 && value[0]) {
                return value[0]; // 返回主要名称
            }
            if (field !== 'name' && typeof value === 'string' && value.trim() !== '') {
                return value;
            }
        }

        // 3. 如果按优先级找不到，则遍历对象中所有可用的语言
        for (const lang in sourceObject) {
            // 确保检查的是对象自身的属性
            if (Object.prototype.hasOwnProperty.call(sourceObject, lang)) {
                const value = sourceObject[lang];
                if (field === 'name' && Array.isArray(value) && value.length > 0 && value[0]) {
                    return value[0];
                }
                if (field !== 'name' && typeof value === 'string' && value.trim() !== '') {
                    return value;
                }
            }
        }

        // 4. 如果没有任何可用翻译，返回最终回退值
        return (field === 'name') ? dataObject.id : t('tooltip_na');
    }

    // 主创建函数，只负责创建不可见对象
    _createNodeObject(node) {
        if (this.nodeObjects.has(node.id)) {
            const nodeObj = this.nodeObjects.get(node.id);
            nodeObj.label.text = node?.name?.['zh-cn']?.[0] || node.id;
            return;
        }

        if (node.x === undefined) { 
            if (this.animationSourceNode) {
                node.x = this.animationSourceNode.x;
                node.y = this.animationSourceNode.y;
            } 
            else if (this.isInitialRender) {
                const quadrantAngleMap = [Math.PI, -Math.PI / 2, 0, Math.PI / 2]; 
                const baseAngle = quadrantAngleMap[this.initialQuadrant];
                const randomAngleInQuadrant = baseAngle + (Math.random() - 0.5) * (Math.PI / 2);
                const viewRadius = Math.max(this.width, this.height);
                const distance = viewRadius * 1.5 + Math.random() * viewRadius * 0.5;
                node.x = this.width / 2 + distance * Math.cos(randomAngleInQuadrant);
                node.y = this.height / 2 + distance * Math.sin(randomAngleInQuadrant);
            }
        }

        const nodeGfx = new PIXI.Graphics();
        nodeGfx.eventMode = 'static';
        nodeGfx.cursor = 'pointer';
        
        // const RESOLUTION_FACTOR = window.devicePixelRatio || 3;
        
        const label = new PIXI.Text({
            text: this._getLocalizedText(node, 'name'),
            style: { 
                fontFamily: 'STXingkai', fontSize: 48,
                fill: 0xffffff, stroke: { color: 0x000000, width: 2, join: 'round' },
                align: 'center',
            }
        });
        label.eventMode = 'none'; 
        label.anchor.set(0.5);
        label.scale.set(1 / 3);

        // 获取当前应有的样式，并应用初始 alpha
        const initialStyles = this._getTargetStyles(node);
        nodeGfx.alpha = initialStyles.alpha;
        label.alpha = initialStyles.alpha;

        if (node.x !== undefined) {
            nodeGfx.position.set(node.x, node.y);
            label.position.set(node.x, node.y);
        }
        
        const nodeObj = { gfx: nodeGfx, label: label, data: node };
        this.nodeObjects.set(node.id, nodeObj);
        this.nodeLayer.addChild(nodeGfx);
        this.nodeLabelLayer.addChild(label);
        
        this._addNodeEvents(nodeObj);
        
        this._redrawNodeBorder(node.id, false);
    }

    // 更新节点的视觉样式
    _updateNodeObject(node) {
        const nodeObj = this.nodeObjects.get(node.id);
        if (!nodeObj) return;

        const isSelected = this.highlightedNodeId === node.id;
        this._redrawNodeBorder(node.id, isSelected);

        // 同时更新标签文本，以备语言切换等场景
        nodeObj.label.text = this._getLocalizedText(node, 'name');
    }

    // 绘制节点的圆圈和边框
    _redrawNodeBorder(nodeId, isSelected, customColor = null) {
        if (this.nodeObjects.has(nodeId)) {
            const obj = this.nodeObjects.get(nodeId);
            const radius = this._getNodeRadius(obj.data);
            const color = parseInt(rgbToHex(this.colorScale(obj.data.type)).substring(1), 16);
            
            const borderColor = customColor !== null ? customColor : (isSelected ? 0xFF0000 : 0xFFFFFF);
            // 统一边框宽度：特殊状态（选中、路径端点）为3px，默认状态为2px
            const borderWidth = (isSelected || customColor) ? 3 : 2;

            obj.gfx.clear().circle(0, 0, radius).fill(color).stroke({ width: borderWidth, color: borderColor });
        }
    }

    _getNodeCharge(node) {
        const base = CONFIG.SIMULATION.CHARGE.BASE_STRENGTH;
        const scale = CONFIG.SIMULATION.CHARGE.DEGREE_SCALE_FACTOR;
        // 节点的度（连接数）越大，其斥力（负值）就越大
        // 使用 Math.sqrt 来平滑度的影响，防止超高度产生过强斥力
        const degree = node.degree || 1;
        return -(base + Math.sqrt(degree) * scale);
    }

    _removeNodeObject(id, isAsync = false) {
        if (!this.nodeObjects.has(id)) return;

        // 在销毁图形对象之前，先触发回调，让控制器检查此节点是否为关键节点。
        this.callbacks.onActiveNodeRemoved(id);

        const { gfx, label, revealTimeouts, fadeTimeouts } = this.nodeObjects.get(id);

        if (isAsync) {
            const destroy = () => {
                if (this.nodeObjects.has(id)) {
                    this.nodeLayer.removeChild(gfx);
                    this.nodeLabelLayer.removeChild(label);
                    gfx.destroy();
                    label.destroy();
                    this.nodeObjects.delete(id);
                }
            };
            // 按照卸载顺序进行淡出
            label.alpha = 0;
            setTimeout(() => {
                gfx.alpha = 0;
                setTimeout(destroy, 0); // 最终销毁
            }, 0);
        }
        else {
            this.nodeLayer.removeChild(gfx);
            this.nodeLabelLayer.removeChild(label);
            gfx.destroy();
            label.destroy();
            this.nodeObjects.delete(id);
        }
    }

    _createLinkObject(link) {
        const linkId = this._getLinkId(link);
        if (this.linkObjects.has(linkId)) return;

        // const RESOLUTION_FACTOR = window.devicePixelRatio || 3;
        const linkGfx = new PIXI.Graphics();
        
        const label = new PIXI.Text({
            text: link.type, 
            style: {
                fontFamily: 'Times New Roman', fontSize: 48,
                fill: 0x888888, stroke: { color: 0x000000, width: 2, join: 'round' },
                align: 'center',
            }
        });
        
        label.anchor.set(0.5);
        const scale = 1 / 6;
        label.scale.set(scale);

        label.eventMode = 'static';
        label.cursor = 'pointer';

        if (label.texture && label.texture.valid) {
            const desiredVisualPadding = 8;
            const padding = desiredVisualPadding / scale;
            label.hitArea = new PIXI.Rectangle(
                -label.width / 2 - padding, -label.height / 2 - padding,
                label.width + padding * 2, label.height + padding * 2
            );
        }

        label.on('mouseover', e => this._handleLinkMouseover(e, link));
        label.on('mouseout', e => this._handleMouseout());
        label.on('mousemove', e => this._handleMousemove(e));

        // 为移动端事件添加监听
        label.on('pointerdown', e => this._handleLinkMouseover(e, link));
        label.on('pointerup', e => this._handleMouseout());
        label.on('pointerout', e => this._handleMouseout());
        
        // 获取当前应有的样式，并应用初始 alpha 和 tint
        const initialStyles = this._getTargetStyles(link);
        linkGfx.alpha = initialStyles.alpha;
        label.alpha = initialStyles.alpha;
        if (initialStyles.tint) {
            linkGfx.tint = initialStyles.tint;
            label.tint = initialStyles.labelTint;
        }

        const linkObj = { gfx: linkGfx, label: label, data: link };
        this.linkObjects.set(linkId, linkObj);
        this.linkLayer.addChild(linkGfx);
        this.linkLabelLayer.addChild(label);
    }

    _drawLink({ gfx, data }) {
        const source = data.source;
        const target = data.target;

        if (source.x === undefined || target.x === undefined) {
            gfx.clear();
            return;
        }
        
        gfx.clear();

        const dx = target.x - source.x;
        const dy = target.y - source.y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist === 0) return;
        
        const targetRadius = this._getNodeRadius(target);
        const endOffset = targetRadius + 2;

        if (data.groupSize <= 1) {
            const endX = target.x - (dx / dist) * endOffset;
            const endY = target.y - (dy / dist) * endOffset;
            gfx.moveTo(source.x, source.y).lineTo(endX, endY);
            this._drawArrowhead(gfx, endX, endY, Math.atan2(dy, dx), data.type);
        }
        else {
            const side = (data.groupIndex % 2 === 0) ? 1 : -1;
            const rank = Math.ceil(data.groupIndex / 2);
            let curvature = rank * 0.15 * side;
            if (source.id > target.id) curvature *= -1;
            
            const midX = (source.x + target.x) / 2;
            const midY = (source.y + target.y) / 2;
            const controlX = midX - curvature * dy;
            const controlY = midY + curvature * dx;

            const cdx = target.x - controlX;
            const cdy = target.y - controlY;
            const cDist = Math.sqrt(cdx*cdx + cdy*cdy);
            if (cDist === 0) return;

            const endX = target.x - (cdx / cDist) * endOffset;
            const endY = target.y - (cdy / cDist) * endOffset;
            
            gfx.moveTo(source.x, source.y).quadraticCurveTo(controlX, controlY, endX, endY);
            this._drawArrowhead(gfx, endX, endY, Math.atan2(endY - controlY, endX - controlX), data.type);
        }

        gfx.stroke({ 
            width: 1, 
            color: 0x888888, 
            alpha: 1.0 
        });
    }

    _drawArrowhead(gfx, x, y, angle, type) {
        if (CONFIG.NON_DIRECTED_LINK_TYPES.has(type)) return;
        const arrowLength = 8;
        const arrowAngle = Math.PI / 6;
        gfx.moveTo(x, y)
        .lineTo(x - arrowLength * Math.cos(angle - arrowAngle), y - arrowLength * Math.sin(angle - arrowAngle));
        gfx.moveTo(x, y)
        .lineTo(x - arrowLength * Math.cos(angle + arrowAngle), y - arrowLength * Math.sin(angle + arrowAngle));
    }

    _removeLinkObject(id, isAsync = false) {
        if (!this.linkObjects.has(id)) return;
        const { gfx, label } = this.linkObjects.get(id);

        if (isAsync) {
            const destroy = () => {
                if (this.linkObjects.has(id)) {
                    this.linkLayer.removeChild(gfx);
                    this.linkLabelLayer.removeChild(label);
                    gfx.destroy();
                    label.destroy();
                    this.linkObjects.delete(id);
                }
            };
            // 按照卸载顺序进行淡出
            label.alpha = 0;
            setTimeout(() => {
                gfx.alpha = 0;
                setTimeout(destroy, 0); // 最终销毁
            }, 0);
        }
        else {
            this.linkLayer.removeChild(gfx);
            this.linkLabelLayer.removeChild(label);
            gfx.destroy();
            label.destroy();
            this.linkObjects.delete(id);
        }
    }

    updateLegend(allNodes) {
        const nodeTypes = [...new Set(allNodes.map(n => n.type))].sort();
        
        const oldDomain = this.colorScale.domain();
        const oldRange = this.colorScale.range();
        const userModifiedColors = new Map();
        oldDomain.forEach((type, i) => {
            userModifiedColors.set(type, oldRange[i]);
        });
    
        const newDomain = [...new Set([...oldDomain, ...nodeTypes])].sort();
        
        const newRange = newDomain.map(type => {
            return userModifiedColors.get(type) || CONFIG.COLORS.NODE_TYPES[type] || "#cccccc";
        });
        
        this.colorScale.domain(newDomain).range(newRange);
        
        const legendContent = d3.select("#legend-container .legend-content");
    
        legendContent.selectAll("div.legend-item")
            .data(nodeTypes, d => d)
            .join(
                enter => {
                    const item = enter.append("div").attr("class", "legend-item");
    
                    item.append("div")
                        .attr("class", "color-box")
                        .style("background-color", d => this.colorScale(d));
                    
                    item.append("input")
                        .attr("type", "checkbox")
                        .attr("checked", true)
                        .on("change", (e, d) => this.callbacks.onLegendToggle(e, d));
                    
                    item.append("span").text(d => d);
                    
                    item.append("input")
                        .attr("type", "color")
                        .attr("value", d => rgbToHex(this.colorScale(d)))
                        .on("input", (event, d) => {
                            const newColor = event.target.value;
                            
                            const domain = this.colorScale.domain();
                            const range = this.colorScale.range();
                            const typeIndex = domain.indexOf(d);
                            if (typeIndex > -1) {
                                range[typeIndex] = newColor;
                                this.colorScale.range(range);
                            }
                            
                            d3.select(event.currentTarget.parentNode).select('.color-box').style('background-color', newColor);
                            
                            this.callbacks.onColorChange();
                        });
                    
                    return item;
                }
            );
    }

    _handleTick() {
        this.nodeObjects.forEach(obj => {
            obj.gfx.position.set(obj.data.x, obj.data.y);
            obj.label.position.set(obj.data.x, obj.data.y);

            // 每次 tick 都更新标签文本以响应语言变化
            obj.label.text = this._getLocalizedText(obj.data, 'name');
        });

        this.linkObjects.forEach(obj => {
            const d = obj.data;
            if (d.source.x === undefined || d.target.x === undefined) return;

            this._drawLink(obj);
            
            let midX = (d.source.x + d.target.x) / 2;
            let midY = (d.source.y + d.target.y) / 2;

            if (d.groupSize > 1) {
                const dx = d.target.x - d.source.x;
                const dy = d.target.y - d.source.y;
                const side = (d.groupIndex % 2 === 0) ? 1 : -1;
                const rank = Math.ceil(d.groupIndex / 2);
                let curvature = rank * 0.15 * side;
                if (d.source.id > d.target.id) curvature *= -1;
                const controlX = midX - curvature * dy;
                const controlY = midY + curvature * dx;
                midX = 0.25 * d.source.x + 0.5 * controlX + 0.25 * d.target.x;
                midY = 0.25 * d.source.y + 0.5 * controlY + 0.25 * d.target.y;
            }
            obj.label.position.set(midX, midY);
        });
    }

    _createDragHandler(nodeObj) {
        const { gfx, data: nodeData } = nodeObj;
        let onDragMove, onDragEnd;
        let dragOffset = { x: 0, y: 0 };
        const onDragStart = (event) => {
            event.nativeEvent.preventDefault();
            this.interactionState.isDown = true;
            this.interactionState.target = nodeData;

            // 拖拽开始时，强制显示Tooltip
            this._handleNodeMouseover(event, nodeData); 

            let hasMoved = false;
            const initialMousePosInWorld = this.world.toLocal(event.global);
            dragOffset.x = nodeData.x - initialMousePosInWorld.x;
            dragOffset.y = nodeData.y - initialMousePosInWorld.y;
            onDragMove = (moveEvent) => {
                if (!hasMoved) {
                    hasMoved = true;
                    this.simulation.alphaTarget(0.3).restart();
                    nodeData.fx = nodeData.x;
                    nodeData.fy = nodeData.y;
                }
                const canvasRect = this.app.canvas.getBoundingClientRect();
                const mouseX_relativeToCanvas = moveEvent.clientX - canvasRect.left;
                const mouseY_relativeToCanvas = moveEvent.clientY - canvasRect.top;
                const globalPoint = new PIXI.Point(mouseX_relativeToCanvas, mouseY_relativeToCanvas);
                const currentMousePosInWorld = this.world.toLocal(globalPoint);
                const newPosX = currentMousePosInWorld.x + dragOffset.x;
                const newPosY = currentMousePosInWorld.y + dragOffset.y;
                nodeData.fx = newPosX;
                nodeData.fy = newPosY;
                gfx.position.set(newPosX, newPosY);
                nodeObj.label.position.set(newPosX, newPosY);

                // 拖拽过程中，强制更新Tooltip位置
                this._handleMousemove(moveEvent);
            };
            onDragEnd = (endEvent) => {
                window.removeEventListener('pointermove', onDragMove);
                window.removeEventListener('pointerup', onDragEnd);
                this.interactionState.isDown = false;

                // 拖拽结束时，手动调用函数隐藏Tooltip
                this._handleMouseout();

                if (!hasMoved) {
                    this.callbacks.onNodeClick(event, nodeData);
                }
                else {
                    this.simulation.alphaTarget(0);
                }
                this.interactionState.target = null;
            };
            window.addEventListener('pointermove', onDragMove);
            window.addEventListener('pointerup', onDragEnd);
        };
        return { onDragStart };
    }

    _addNodeEvents(nodeObj) {
        const { gfx, data } = nodeObj;
        const handler = this._createDragHandler(nodeObj);
        gfx.on('pointerdown', handler.onDragStart);
        gfx.on('pointermove', (e) => this._handleMousemove(e));
        gfx.on('mouseover', (e) => this._handleNodeMouseover(e, data));
        gfx.on('mouseout', (e) => !this.interactionState.isDown && this._handleMouseout());
    }

    _initCameraControls() {
        const canvas = this.app.canvas;
        let hasCameraMoved = false;
        const onPointerMove = (e) => {
            if (this.camera.dragging) {
                if (Math.abs(e.clientX - this.camera.lastX) > 1 || Math.abs(e.clientY - this.camera.lastY) > 1) {
                    hasCameraMoved = true;
                }
                const dx = e.clientX - this.camera.lastX;
                const dy = e.clientY - this.camera.lastY;
                this.world.x += dx;
                this.world.y += dy;
                this.camera.lastX = e.clientX;
                this.camera.lastY = e.clientY;

                // 如果当前有可见描述框，就调用函数，让它的位置跟随手指/鼠标移动
                if (this.tooltip.style.opacity === '1') {
                    this._handleMousemove(e);
                }
            }
        };
        const onPointerUp = (e) => {
            if (this.camera.dragging) {
                if (!hasCameraMoved) {
                    this.callbacks.onSvgClick(e);
                }
                this.camera.dragging = false;
                window.removeEventListener('pointermove', onPointerMove);
                window.removeEventListener('pointerup', onPointerUp);
            }
        };
        canvas.addEventListener('pointerdown', e => {
            e.preventDefault();
            if (this.interactionState.isDown || e.target !== canvas) return;
            this.camera.dragging = true;
            hasCameraMoved = false;
            this.camera.lastX = e.clientX;
            this.camera.lastY = e.clientY;
            window.addEventListener('pointermove', onPointerMove);
            window.addEventListener('pointerup', onPointerUp);
        });
        
        canvas.addEventListener('wheel', e => {
            e.preventDefault();
            const zoomFactor = e.deltaY > 0 ? 0.9 : 1.1;
            const mousePoint = new PIXI.Point(e.clientX, e.clientY);
            const mouseInWorld = this.world.toLocal(mousePoint);
            const newScale = Math.max(0.1, Math.min(5, this.world.scale.x * zoomFactor));
            this.world.scale.set(newScale);
            this.world.position.x = mousePoint.x - mouseInWorld.x * newScale;
            this.world.position.y = mousePoint.y - mouseInWorld.y * newScale;
        });

        // --- 移动端触摸缩放 ---

        let pinching = false;
        let lastPinchDistance = 0;

        const getDistance = (touches) => {
            const dx = touches[0].clientX - touches[1].clientX;
            const dy = touches[0].clientY - touches[1].clientY;
            return Math.sqrt(dx * dx + dy * dy);
        };

        const getMidpoint = (touches) => {
            return {
                x: (touches[0].clientX + touches[1].clientX) / 2,
                y: (touches[0].clientY + touches[1].clientY) / 2,
            };
        };

        canvas.addEventListener('touchstart', e => {
            if (e.touches.length === 2) {
                e.preventDefault(); // 阻止浏览器默认的缩放行为
                pinching = true;
                this.camera.dragging = false; // 阻止单指拖拽平移
                lastPinchDistance = getDistance(e.touches);
            }
        }, { passive: false });

        canvas.addEventListener('touchmove', e => {
            if (pinching && e.touches.length === 2) {
                e.preventDefault();
                
                const newDistance = getDistance(e.touches);
                const zoomFactor = newDistance / lastPinchDistance;
                lastPinchDistance = newDistance;

                const midpoint = getMidpoint(e.touches);
                const point = new PIXI.Point(midpoint.x, midpoint.y);
                const pointInWorld = this.world.toLocal(point);
                
                const newScale = Math.max(0.1, Math.min(5, this.world.scale.x * zoomFactor));
                this.world.scale.set(newScale);

                this.world.position.x = point.x - pointInWorld.x * newScale;
                this.world.position.y = point.y - pointInWorld.y * newScale;
            }
        }, { passive: false });

        const onTouchEnd = (e) => {
            if (e.touches.length < 2) {
                pinching = false;
                lastPinchDistance = 0;
            }
        };

        canvas.addEventListener('touchend', onTouchEnd);
        canvas.addEventListener('touchcancel', onTouchEnd);
    }

    _updateCameraAnimation() {
        if (!this.camera.animation) return;
        const anim = this.camera.animation;
        const now = performance.now();
        let progress = (now - anim.startTime) / anim.duration;
        progress = Math.min(progress, 1);
        const easedProgress = easeInOutQuad(progress);
        const currentX = anim.startX + (anim.targetX - anim.startX) * easedProgress;
        const currentY = anim.startY + (anim.targetY - anim.startY) * easedProgress;
        const currentScale = anim.startScale + (anim.targetScale - anim.startScale) * easedProgress;
        this.world.position.set(currentX, currentY);
        this.world.scale.set(currentScale);
        if (progress >= 1) {
            this.camera.animation = null;
        }
    }

    centerOnNode(nodeData, scale = 1.5, duration = 750) {
        if (!nodeData?.x || !nodeData?.y) return;
        const targetX = this.width / 2 - nodeData.x * scale;
        const targetY = this.height / 2 - nodeData.y * scale;
        this.camera.animation = {
            startX: this.world.position.x, startY: this.world.position.y,
            startScale: this.world.scale.x, targetX, targetY, targetScale: scale,
            startTime: performance.now(), duration: duration
        };
    }

    setInitialView() {
        this.centerOnNode({ x: this.width/2, y: this.height/2 }, CONFIG.INITIAL_ZOOM, CONFIG.INITIAL_ZOOM_DURATION);
    }

    _animateToTargets(targets) {
        const DURATION = 500;
        const allObjects = [...this.nodeObjects.values(), ...this.linkObjects.values()];
        allObjects.forEach(obj => {
            let interactiveObject;
            if (obj.data.source) { interactiveObject = obj.label; }
            else { interactiveObject = obj.gfx; }
            const target = targets.get(obj);
            if (!target) return;
            if (target.eventMode !== undefined && interactiveObject) {
                interactiveObject.eventMode = target.eventMode;
            }
            if (obj.animationTimeout) clearTimeout(obj.animationTimeout);
            if (obj.tintAnimationTimeout) clearTimeout(obj.tintAnimationTimeout);
            const gfx = obj.gfx;
            const label = obj.label;
            const isGfxAlphaCorrect = Math.abs(gfx.alpha - target.alpha) < 0.01;
            const isLabelAlphaCorrect = !label || Math.abs(label.alpha - target.alpha) < 0.01;
            const isAlphaCorrect = isGfxAlphaCorrect && isLabelAlphaCorrect;

            const isGfxTintCorrect = target.tint === undefined || gfx.tint === target.tint;
            const isLabelTintCorrect = target.labelTint === undefined || !label || label.tint === target.labelTint;
            const isTintCorrect = isGfxTintCorrect && isLabelTintCorrect;

            if (isAlphaCorrect && isTintCorrect) { return; }
            const isFadedLinkBecomingActive = obj.data.source && target.alpha === 1 && gfx.alpha < 0.5;
            if (isFadedLinkBecomingActive) {
                gfx.alpha = gfx.alpha * 2;
                label.alpha = label.alpha * 2;
                obj.animationTimeout = setTimeout(() => {
                    gfx.alpha = target.alpha;
                    label.alpha = target.alpha;
                    obj.tintAnimationTimeout = setTimeout(() => {
                        if (target.tint !== undefined) gfx.tint = target.tint;
                        if (target.labelTint !== undefined && label) label.tint = target.labelTint;
                    }, DURATION / 3);
                }, DURATION / 2);
            }
            else {
                gfx.alpha = (gfx.alpha + target.alpha) / 2;
                label.alpha = (label.alpha + target.alpha) / 2;
                obj.animationTimeout = setTimeout(() => {
                    gfx.alpha = target.alpha;
                    label.alpha = target.alpha;
                    if (target.tint !== undefined) gfx.tint = target.tint;
                    if (target.labelTint !== undefined && label) label.tint = target.labelTint;
                }, DURATION / 2);
            }
        });
    }

    _getTargetStyles(itemData) {
        const selectedNodeId = this.highlightedNodeId;
        const neighbors = this.highlightedNeighbors;

        if (!selectedNodeId) {
            return itemData.source ? this.styleStates.LINK_DEFAULT : this.styleStates.NODE_DEFAULT;
        }

        const activeNodeIds = new Set([selectedNodeId, ...(neighbors[selectedNodeId] || [])]);

        if (!itemData.source) {
            // Is a Node
            return activeNodeIds.has(itemData.id) ? 
                   this.styleStates.NODE_DEFAULT : 
                   this.styleStates.NODE_FADED;
        }
        else {
            // Is a Link
            const isRelated = (itemData.source.id === selectedNodeId || itemData.target.id === selectedNodeId);
            if (isRelated) {
                const nodeData = this.nodeObjects.get(selectedNodeId)?.data;
                const highlightColor = nodeData ? parseInt(rgbToHex(this.colorScale(nodeData.type)).substring(1), 16) : 0xFFFFFF;
                
                return { 
                    ...this.styleStates.LINK_HIGHLIGHT, 
                    tint: highlightColor, // 动态计算关系线颜色
                    eventMode: 'static'
                };
            }
            else {
                return this.styleStates.LINK_FADED;
            }
        }
    }

    _animateToCurrentHighlightState() {
        const targets = new Map();
        
        this.nodeObjects.forEach(obj => {
            targets.set(obj, this._getTargetStyles(obj.data));
        });
        this.linkObjects.forEach(obj => {
            targets.set(obj, this._getTargetStyles(obj.data));
        });

        this._animateToTargets(targets);
    }

    updateHighlights(selectedNodeId, neighbors) {
        const previousNodeId = this.highlightedNodeId;
        
        // 1. 存储新的高亮状态
        this.highlightedNodeId = selectedNodeId;
        this.highlightedNeighbors = neighbors;

        // 2. 应用所有视觉样式（透明度和颜色）
        this._animateToCurrentHighlightState();

        // 3. 处理边框样式的切换
        if (previousNodeId && previousNodeId !== selectedNodeId) {
            this._redrawNodeBorder(previousNodeId, false);
        }
        if (selectedNodeId) {
            this._redrawNodeBorder(selectedNodeId, true);
        }
    }

    highlightPaths(paths, sourceId, targetId, sourceNode) {
        this.stopPathAnimation();
        this.clearAllHighlights();
        const sourceNodeData = sourceNode || this.nodeObjects.get(sourceId)?.data;
        const highlightColor = sourceNodeData ? parseInt(rgbToHex(this.colorScale(sourceNodeData.type)).substring(1), 16) : 0xFFFFFF;
        if (paths.length === 0) {
            const targets = new Map();
            this.nodeObjects.forEach(obj => targets.set(obj, {
                alpha: 0.1,
                eventMode: 'none'
            }));
            this.linkObjects.forEach(obj => targets.set(obj, {
                alpha: 0.05,
                eventMode: 'none',
                tint: 0xFFFFFF,
                labelTint: 0xFFFFFF
            }));
            const sourceObj = this.nodeObjects.get(sourceId);
            const targetObj = this.nodeObjects.get(targetId);
            if(sourceObj) targets.set(sourceObj, { 
                alpha: 1, 
                eventMode: 'static' 
            });
            if(targetObj) targets.set(targetObj, { 
                alpha: 1, 
                eventMode: 'static' 
            });
            this._animateToTargets(targets);
            this._redrawPathBorders(sourceId, targetId);
            return;
        }
        const visitedNodeIds = new Set();
        const visitedLinkObjects = new Set();
        const animatePath = (path) => {
            const targets = new Map();
            this.nodeObjects.forEach(obj => targets.set(obj, {
                alpha: 0.1,
                eventMode: 'none'
            }));
            this.linkObjects.forEach(obj => targets.set(obj, {
                alpha: 0.05,
                eventMode: 'none',
                tint: 0x888888,
                labelTint: 0x888888
            }));
            visitedNodeIds.forEach(nodeId => {
                const nodeObj = this.nodeObjects.get(nodeId);
                if (nodeObj) targets.set(nodeObj, { 
                    alpha: 1, 
                    eventMode: 'static' 
                });
            });
            visitedLinkObjects.forEach(linkObj => {
                targets.set(linkObj, { 
                    alpha: 1, 
                    eventMode: 'static', 
                    tint: 0x888888, 
                    labelTint: 0x888888 
                });
            });
            path.forEach(nodeId => {
                visitedNodeIds.add(nodeId);
                const nodeObj = this.nodeObjects.get(nodeId);
                if (nodeObj) targets.set(nodeObj, { 
                    alpha: 1, 
                    eventMode: 'static' 
                });
            });
            for (let i = 0; i < path.length - 1; i++) {
                const u = path[i];
                const v = path[i + 1];
                for (const linkObj of this.linkObjects.values()) {
                    const d = linkObj.data;
                    if ((d.source.id === u && d.target.id === v) || (d.source.id === v && d.target.id === u)) {
                        visitedLinkObjects.add(linkObj);
                        targets.set(linkObj, { 
                            alpha: 1, 
                            eventMode: 'static', 
                            tint: highlightColor, 
                            labelTint: 0xFFFFFF 
                        });
                    }
                }
            }
            this._animateToTargets(targets);
            this._redrawPathBorders(sourceId, targetId);
        };
        paths.forEach((path, i) => {
            const timerId = setTimeout(() => {
                animatePath(path);
            }, i * 2500);
            this.pathAnimationTimers.push(timerId);
        });
    }

    _redrawPathBorders(sourceId, targetId) {
        const DURATION = 150;
        setTimeout(() => {
            this._redrawNodeBorder(sourceId, true, 0xffdd00);
            this._redrawNodeBorder(targetId, true, 0xff6600);
        }, DURATION);
    }

    stopPathAnimation() {
        this.pathAnimationTimers.forEach(timerId => clearTimeout(timerId));
        this.pathAnimationTimers = [];
    }

    clearAllHighlights() {
        const targets = new Map();
        this.nodeObjects.forEach(obj => {
            targets.set(obj, { 
                alpha: 1, 
                eventMode: 'static' 
            });
            this._redrawNodeBorder(obj.data.id, false);
        });
        this.linkObjects.forEach(obj => {
            targets.set(obj, { 
                alpha: 1, 
                eventMode: 'static', 
                tint: 0x888888, 
                labelTint: 0x888888 
            });
        });
        this._animateToTargets(targets);
        this.highlightedNodeId = null;
    }

    hideTooltip() {
        this.tooltip.style.opacity = '0';
    }

    _getNodeRadius(node) {
        return CONFIG.NODE_RADIUS.BASE + Math.sqrt(node.degree || 1) * CONFIG.NODE_RADIUS.SCALE;
    }

    _handleNodeMouseover(event, data) {
        if (this.interactionState.isDown && this.interactionState.target !== data) {
            return;
        }

        const displayName = this._getLocalizedText(data, 'name');
        const description = this._getLocalizedText(data, 'description');
        
        this.tooltip.style.opacity = "1";
        this.tooltip.innerHTML = 
            `<strong>${t('tooltip_name')}</strong> ${displayName}<br>` +
            `<strong>${t('tooltip_id')}</strong> ${data.id}<br>` +
            `<strong>${t('tooltip_type')}</strong> ${data.type}<br>` +
            `<strong>${t('tooltip_degree')}</strong> ${data.degree || 0}<br>` +
            `<strong>${t('tooltip_desc')}</strong> ${description}`;
        this._handleMousemove(event);
    }

    _handleLinkMouseover(event, data) {
        if (this.interactionState.isDown && this.interactionState.target !== data) {
            return;
        }
        // 使用辅助函数获取源和目标节点的名称
        const sourceName = this._getLocalizedText(data.source, 'name');
        const targetName = this._getLocalizedText(data.target, 'name');
        const description = this._getLocalizedText(data, 'description');
        
        this.tooltip.style.opacity = "1";
        this.tooltip.innerHTML = 
            `${sourceName} ${data.type} ${targetName}<br>` +
            `<strong>${t('tooltip_desc')}</strong> ${description}`;
        this._handleMousemove(event);
    }

    _handleMouseout() { this.tooltip.style.opacity = "0"; }
    
    _handleMousemove(event) {
        // 兼容PIXI事件对象 (event.nativeEvent) 和原生DOM事件对象 (event)
        const originalEvent = event.nativeEvent || event;
        
        // 确保拥有有效的事件对象和坐标
        if (!originalEvent || typeof originalEvent.pageX === 'undefined') {
            return;
        }

        const x = originalEvent.pageX;
        const y = originalEvent.pageY;
        this.tooltip.style.left = `${x + 15}px`;
        this.tooltip.style.top = `${y + 15}px`;
    }

    _createSimulation() {
        const getLinkCategory = (link) => {
            if (CONFIG.RELATIONSHIP_CATEGORIES.CLOSE.has(link.type)) {
                return 'CLOSE';
            }
            if (CONFIG.RELATIONSHIP_CATEGORIES.DISTANT.has(link.type)) {
                return 'DISTANT';
            }
            return 'NORMAL';
        };

        return d3.forceSimulation()
            .force("link", d3.forceLink().id(d => d.id)
                // 根据关系类别调整 link distance
                .distance(link => {
                    const category = getLinkCategory(link);
                    const modifier = CONFIG.LINK_MODIFIERS[category].distance;
                    return CONFIG.SIMULATION.LINK.BASE_DISTANCE + modifier;
                })
                // 根据关系类别调整 link strength
                .strength(link => {
                    const category = getLinkCategory(link);
                    const modifier = CONFIG.LINK_MODIFIERS[category].strength;
                    return CONFIG.SIMULATION.LINK.STRENGTH + modifier;
                })
            )
            .force("charge", d3.forceManyBody().strength(node => this._getNodeCharge(node)))
            .force("x", d3.forceX(this.width / 2).strength(CONFIG.SIMULATION.CENTER_X_STRENGTH))
            .force("y", d3.forceY(this.height / 2).strength(CONFIG.SIMULATION.CENTER_Y_STRENGTH))
            .force("collide", d3.forceCollide().radius(d => {
                return this._getNodeRadius(d) + 10; 
            }).strength(0.3));
    }

    _updateSimulation(graphData) {
        const { visibleNodes, validRels } = graphData;
        
        visibleNodes.forEach(n => n.isAnchor = false);
        [...visibleNodes].sort((a, b) => b.degree - a.degree).slice(0, 3).forEach(n => n.isAnchor = true);
        
        const linkGroups = {};
        validRels.forEach(link => {
            const pairId = link.source.id < link.target.id ? `${link.source.id}-${link.target.id}` : `${link.target.id}-${link.source.id}`;
            if (!linkGroups[pairId]) linkGroups[pairId] = [];
            linkGroups[pairId].push(link);
        });
        
        // 对每个分组内的链接进行确定性排序，以获得稳定的 groupIndex
        Object.values(linkGroups).forEach(group => {
            group.sort((a, b) => {
                if (a.type < b.type) return -1;
                if (a.type > b.type) return 1;
                if (a.source.id < b.source.id) return -1;
                if (a.source.id > b.source.id) return 1;
                if (a.target.id < b.target.id) return -1;
                if (a.target.id > b.target.id) return 1;
                return 0;
            });
        
            const groupSize = group.length;
            group.forEach((link, index) => {
                link.groupSize = groupSize;
                link.groupIndex = index;
            });
        });
        
        this.simulation.nodes(visibleNodes);
        this.simulation.force("link").links(validRels);
        // 设置一个大于0的目标alpha值，以防模拟完全停止
        this.simulation.alpha(this.isInitialRender ? CONFIG.SIMULATION.INITIAL_ALPHA : CONFIG.SIMULATION.REHEAT_ALPHA).alphaTarget(0.1).restart();
        
        this.animationSourceNode = null;
    }
}
