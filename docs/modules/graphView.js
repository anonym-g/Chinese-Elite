// docs/modules/graphView.js
import CONFIG from './config.js';
import { rgbToHex } from './utils.js';

/**
 * GraphView 类负责所有与D3.js和SVG相关的渲染任务。
 * 它不关心应用的状态，只负责接收格式化的数据并将其绘制到屏幕上。
 */
export class GraphView {
    constructor(selector, callbacks) {
        this.svg = d3.select(selector);
        this.width = this.svg.node().getBoundingClientRect().width;
        this.height = this.svg.node().getBoundingClientRect().height;
        this.tooltip = d3.select(".tooltip");
        
        // callbacks 用于将视图中的事件通知给外部控制器，实现解耦
        // 例如，当一个节点被点击时，视图本身不修改全局状态，而是调用 onNodeClick 回调
        this.callbacks = callbacks; // { onNodeClick, onSvgClick, onLegendToggle, onColorChange }
        
        // 创建不同的SVG分组，用于控制渲染层级
        this.container = this.svg.append("g");

        // 用于存放隐形关系线的图层
        // this.linkHoverGroup = this.container.append("g").attr("class", "link-hover-area");
        this.linkGroup = this.container.append("g").attr("class", "links");
        this.linkLabelGroup = this.container.append("g").attr("class", "link-labels");
        this.nodeGroup = this.container.append("g").attr("class", "nodes");
        
        this.colorScale = d3.scaleOrdinal(d3.schemeCategory10);
        this.zoom = d3.zoom().on("zoom", (event) => this.container.attr("transform", event.transform));
        
        this.simulation = this._createSimulation();
        this._setupDefsAndZoom();
    }
    
    /**
     * 根据所有节点数据动态创建图例。
     * @param {Array} allNodes - 完整节点列表
     */
    createLegend(allNodes) {
        const nodeTypes = [...new Set(allNodes.map(n => n.type))];
        const colorRange = nodeTypes.map(type => CONFIG.COLORS.NODE_TYPES[type] || null);
        this.colorScale.domain(nodeTypes).range(colorRange.filter(c => c)).unknown("#cccccc");
        
        const legendContent = d3.select("#legend-container .legend-content");
        legendContent.selectAll("*").remove();

        d3.select(".legend-toggle").on("click", () => {
            document.getElementById('legend-container').classList.toggle("collapsed");
        });

        nodeTypes.forEach(type => {
            const item = legendContent.append("div").attr("class", "legend-item");
            const colorBox = item.append("div").attr("class", "color-box").style("background-color", this.colorScale(type));
            
            item.append("input")
                .attr("type", "checkbox")
                .attr("checked", true)
                .on("change", (e) => this.callbacks.onLegendToggle(e, type, colorBox));
            
            item.append("span").text(type);
            
            item.append("input")
                .attr("type", "color")
                .attr("value", rgbToHex(this.colorScale(type)))
                .on("input", (event) => {
                    const newColor = event.target.value;
                    const newRange = this.colorScale.range();
                    const typeIndex = this.colorScale.domain().indexOf(type);
                    if (typeIndex > -1) {
                        newRange[typeIndex] = newColor;
                        this.colorScale.range(newRange);
                    }
                    colorBox.style("background-color", newColor);
                    // 通知外部颜色已改变，以便重新渲染
                    this.callbacks.onColorChange();
                });
        });
    }
    
    /**
     * 主渲染函数。使用D3的数据绑定模式更新DOM。
     * @param {object} graphData - 包含 { visibleNodes, validRels } 的对象
     */
    render(graphData) {
        const { visibleNodes, validRels } = graphData;
        
        // 锚点逻辑：将度数最高的几个节点设为锚点，使其在布局中更稳定
        visibleNodes.forEach(n => n.isAnchor = false);
        [...visibleNodes].sort((a, b) => b.degree - a.degree).slice(0, 3).forEach(n => n.isAnchor = true);

        // 分组逻辑：处理两个节点间存在多条边的情况，将它们渲染成弧线
        const linkGroups = {};
        validRels.forEach(link => {
            const pairId = link.source.id < link.target.id ? `${link.source.id}-${link.target.id}` : `${link.target.id}-${link.source.id}`;
            if (!linkGroups[pairId]) linkGroups[pairId] = [];
            linkGroups[pairId].push(link);
        });
        validRels.forEach(link => {
            const pairId = link.source.id < link.target.id ? `${link.source.id}-${link.target.id}` : `${link.target.id}-${link.source.id}`;
            link.groupSize = linkGroups[pairId].length;
            link.groupIndex = linkGroups[pairId].indexOf(link);
        });

        // 绑定可见的样式线
        this.linkGroup.selectAll("path.link")
            .data(validRels, d => `${d.source.id}-${d.target.id}-${d.type}`)
            .join("path")
            .attr("class", "link")
            .attr("marker-end", d => !CONFIG.NON_DIRECTED_LINK_TYPES.has(d.type) ? "url(#arrowhead)" : null);

        // 关系标签渲染逻辑
        // 1. 将数据绑定到<g>元素上，每个<g>代表一个完整的“标签单元”
        const linkLabelUnits = this.linkLabelGroup.selectAll("g.link-label-unit")
            .data(validRels, d => `${d.source.id}-${d.target.id}-${d.type}`)
            .join(
                enter => {
                    // 为新数据创建<g>容器
                    const g = enter.append("g").attr("class", "link-label-unit");
                    // 在<g>内部，先添加一个矩形作为背景和热区
                    g.append("rect");
                    // 再添加文本
                    g.append("text").attr("class", "link-label").text(d => d.type);
                    return g;
                }
            );

        // 2. 将鼠标事件绑定到整个<g>分组上
        linkLabelUnits
            .on("mouseover", this._handleLinkMouseover.bind(this))
            .on("mousemove", this._handleMousemove.bind(this))
            .on("mouseout", this._handleMouseout.bind(this));
        
        // 3. 动态计算每个文字的尺寸，并调整其背后的矩形大小
        linkLabelUnits.each(function() {
            // 'this' 在这里指向当前的<g>元素
            const textNode = d3.select(this).select("text").node();
            try {
                const bbox = textNode.getBBox(); // 获取文字的边界框
                const padding = 2; // 设置矩形比文字大一点的内边距

                d3.select(this).select("rect")
                    .attr("x", bbox.x - padding)
                    .attr("y", bbox.y - padding)
                    .attr("width", bbox.width + (padding * 2))
                    .attr("height", bbox.height + (padding * 2))
                    .attr("rx", 2) // 可选：给矩形加上小圆角
                    .attr("ry", 2);
            } catch (e) {
                // 在某些罕见情况下 (如元素不可见)，getBBox可能会失败
                console.error("Could not get BBox for text label", textNode.textContent, e);
            }
        });

        // 绑定节点数据
        const nodeElements = this.nodeGroup.selectAll("g.node")
            .data(visibleNodes, d => d.id)
            .join(
                enter => {
                    // 对于新加入的节点
                    const g = enter.append("g").attr("class", "node");
                    g.append("circle").on("click", (e, d) => this.callbacks.onNodeClick(e, d));
                    g.append("text").attr("dy", ".3em").text(d => d.id);
                    return g;
                },
                update => update,
                exit => exit.remove()
            )
            .call(this._createDragHandler());
        
        nodeElements.select("circle")
            .transition().duration(200)
            .attr("r", d => this._getNodeRadius(d))
            .attr("fill", d => this.colorScale(d.type));

        nodeElements
            .on("mouseover", this._handleNodeMouseover.bind(this))
            .on("mousemove", this._handleMousemove.bind(this))
            .on("mouseout", this._handleMouseout.bind(this));
        
        this.simulation.nodes(visibleNodes);
        this.simulation.force("link").links(validRels);
        this.simulation.alpha(0.3).restart();
    }

    /**
     * 更新节点和连线的高亮状态（用于单击节点）。
     * @param {string | null} selectedNodeId - 当前选中的节点ID
     * @param {object} neighbors - 邻接信息
     */
    updateHighlights(selectedNodeId, neighbors) {
        // 在函数开头，强制清除所有路径高亮相关的样式
        // 这确保了从“路径高亮”模式切换回“节点高亮”模式时，旧样式被彻底清除
        this.nodeGroup.selectAll('.node')
            .classed('path-highlight', false)
            .classed('path-source', false)
            .classed('path-target', false)
            .select('circle').style('stroke', null); // 移除内联的stroke颜色

        this.linkGroup.selectAll('.link')
            .classed('path-highlight', false)
            .style('stroke', null); // 移除内联的stroke颜色

        // --- 开始正常的节点高亮逻辑 ---
        const isHighlighting = selectedNodeId !== null;

        this.nodeGroup.selectAll('.node')
            .classed('faded', isHighlighting && (d => d.id !== selectedNodeId && !neighbors[selectedNodeId]?.includes(d.id)))
            .classed('highlight', d => d.id === selectedNodeId);
        
        this.linkGroup.selectAll('.link')
            .classed('faded', isHighlighting && (d => d.source.id !== selectedNodeId && d.target.id !== selectedNodeId))
            .style('stroke', d => {
                const isRelated = isHighlighting && (d.source.id === selectedNodeId || d.target.id === selectedNodeId);
                if (isRelated) {
                    const selectedNode = this.simulation.nodes().find(n => n.id === selectedNodeId);
                    return selectedNode ? this.colorScale(selectedNode.type) : CONFIG.COLORS.DEFAULT_LINK;
                }
                return null;
            });

        this.linkLabelGroup.selectAll('.link-label-unit')
            .classed('faded', isHighlighting && (d => d.source.id !== selectedNodeId && d.target.id !== selectedNodeId));
        
        const selectedNode = isHighlighting ? this.simulation.nodes().find(n => n.id === selectedNodeId) : null;
        const highlightColor = selectedNode ? this.colorScale(selectedNode.type) : CONFIG.COLORS.DEFAULT_ARROW;
        this.svg.select("#arrowhead path").style('fill', highlightColor);
    }
    
    /**
     * 高亮显示找到的路径。
     * @param {Array<Array<string>>} paths - 路径数组
     * @param {string} sourceId - 源节点ID
     * @param {string} targetId - 目标节点ID
     * @param {object} sourceNode - 源节点对象
     */
    highlightPaths(paths, sourceId, targetId, sourceNode) {
        this.clearAllHighlights();
        
        const highlightColor = this.colorScale(sourceNode ? sourceNode.type : 'default');

        // 确保所有元素都被黯淡，包括 link-label-unit
        this.nodeGroup.selectAll(".node").classed("faded", true);
        this.linkGroup.selectAll(".link").classed("faded", true);
        this.linkLabelGroup.selectAll(".link-label-unit").classed("faded", true); // 使用正确的选择器
        
        // 动态地、逐条地展示路径
        paths.forEach((path, i) => {
            setTimeout(() => {
                // 清除上一条路径的高亮，为显示下一条做准备
                this.nodeGroup.selectAll(".node.path-highlight").classed('path-highlight path-source path-target', false).select('circle').style('stroke', null);
                this.linkGroup.selectAll(".link.path-highlight").classed('path-highlight', false).style('stroke', null);
                
                const pathNodeIds = new Set(path);
                
                // 高亮当前路径的节点
                this.nodeGroup.selectAll('.node')
                    .filter(d => pathNodeIds.has(d.id))
                    .classed('faded', false)
                    .classed('path-highlight', true)
                    .classed('path-source', d => d.id === sourceId)
                    .classed('path-target', d => d.id === targetId)
                    .select('circle').style('stroke', highlightColor);
                
                // 高亮当前路径的边
                for (let j = 0; j < path.length - 1; j++) {
                    const source = path[j];
                    const target = path[j + 1];

                    // 高亮路径上的关系线
                    this.linkGroup.selectAll('.link')
                        .filter(d => (d.source.id === source && d.target.id === target) || (d.source.id === target && d.target.id === source))
                        .classed('faded', false)
                        .classed('path-highlight', true)
                        .style('stroke', highlightColor);
                    
                    // 高亮路径上的关系文字
                    this.linkLabelGroup.selectAll('.link-label-unit')
                        .filter(d => (d.source.id === source && d.target.id === target) || (d.source.id === target && d.target.id === source))
                        .classed('faded', false);
                }
                
                this.svg.select("#arrowhead path").style('fill', highlightColor);
            }, i * 2000); // 每2秒显示一条路径
        });
    }

    /**
     * 清除所有的高亮效果，恢复默认视图。
     */
    clearAllHighlights() {
        this.nodeGroup.selectAll(".node").classed("faded", false).classed("highlight", false).classed('path-highlight path-source path-target', false).select('circle').style('stroke', null);
        this.linkGroup.selectAll(".link").classed("faded", false).classed('path-highlight', false).style("stroke", null);
        this.linkLabelGroup.selectAll(".link-label").classed("faded", false);
        this.svg.select("#arrowhead path").style('fill', CONFIG.COLORS.DEFAULT_ARROW);
    }

    /**
     * 将视图平移和缩放，使指定节点居中。
     * @param {object} nodeData - 节点数据对象
     */
    centerOnNode(nodeData) {
        const scale = 1.5;
        const x = this.width / 2 - nodeData.x * scale;
        const y = this.height / 2 - nodeData.y * scale;
        this.svg.transition().duration(750).call(this.zoom.transform, d3.zoomIdentity.translate(x, y).scale(scale));
    }

    // --- "私有" 辅助方法 ---

    _createSimulation() {
        const simulation = d3.forceSimulation()
            .force("link", d3.forceLink().id(d => d.id).distance(CONFIG.SIMULATION.LINK_DISTANCE))
            .force("charge", d3.forceManyBody().strength(CONFIG.SIMULATION.CHARGE_STRENGTH))
            .force("x", d3.forceX(this.width / 2).strength(d => d.isAnchor ? CONFIG.SIMULATION.ANCHOR_STRENGTH : CONFIG.SIMULATION.CENTER_X_STRENGTH))
            .force("y", d3.forceY(this.height / 2).strength(d => d.isAnchor ? CONFIG.SIMULATION.ANCHOR_STRENGTH : CONFIG.SIMULATION.CENTER_Y_STRENGTH));
        
        simulation.on("tick", this._handleTick.bind(this));
        return simulation;
    }

    /**
     * 初始化SVG的defs（用于定义箭头等可复用元素）和zoom（缩放/平移）行为。
     * @private
     */
    _setupDefsAndZoom() {
        // --- 箭头定义 ---
        // 在<defs>中定义一个<marker>元素，作为连线的箭头
        this.svg.append('defs').append('marker')
            .attr('id', 'arrowhead')
            .attr('viewBox', '-10 -5 10 10')
            .attr('refX', 0)
            .attr('refY', 0)
            .attr('orient', 'auto')
            .attr('markerWidth', 8)
            .attr('markerHeight', 8)
            .attr('xoverflow', 'visible')
            .append('svg:path')
            .attr('d', 'M -10,-5 L 0,0 L -10,5')
            .attr('class', 'arrowhead-path')
            .style('fill', CONFIG.COLORS.DEFAULT_ARROW);
            
        // --- Zoom/Pan 事件处理 ---
        // 为d3.zoom()行为绑定"zoom"事件的监听器
        this.zoom.on("zoom", (event) => {
            // 1. 应用平移和缩放变换到主容器<g>上
            this.container.attr("transform", event.transform);

            // 2. 在拖拽平移图谱时，同步更新提示框的位置
            // event.sourceEvent 保存了触发zoom的原始DOM事件（如mousemove）
            if (event.sourceEvent) {
                // 为略微提高性能，只在提示框当前可见时 (opacity为"1") 才执行位置更新
                if (this.tooltip.style("opacity") === "1") {
                    // 从原始事件中获取鼠标的页面坐标，并更新提示框的CSS left和top属性
                    this.tooltip.style("left", (event.sourceEvent.pageX + 10) + "px")
                               .style("top", (event.sourceEvent.pageY + 10) + "px");
                }
            }
        });

        // 将配置好的zoom行为应用到整个SVG画布上
        // 同时保留画布的点击事件，用于在点击空白处时取消节点高亮
        this.svg.call(this.zoom).on("click", (e) => this.callbacks.onSvgClick(e));
    }
    
    // _handleTick 同时更新可见线和隐形区域的位置
    _handleTick() {
        this.linkGroup.selectAll("path.link").attr("d", d => this._calculateLinkPath(d));
        // 将transform应用到g.link-label-unit上
        this.linkLabelGroup.selectAll("g.link-label-unit").attr("transform", d => this._calculateLinkLabelTransform(d));
        this.nodeGroup.selectAll("g.node").attr("transform", d => `translate(${d.x},${d.y})`);
    }

    _getNodeRadius(node) {
        return CONFIG.NODE_RADIUS.BASE + Math.sqrt(node.degree || 1) * CONFIG.NODE_RADIUS.SCALE;
    }
    
    _createDragHandler() {
        const dragstarted = (event, d) => {
            if (!event.active) this.simulation.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
        };
        const dragged = (event, d) => {
            d.fx = event.x;
            d.fy = event.y;
            this.tooltip.style("left", (event.sourceEvent.pageX + 10) + "px").style("top", (event.sourceEvent.pageY + 10) + "px");
        };
        const dragended = (event, d) => {
            if (!event.active) this.simulation.alphaTarget(0);
            if (!d.isAnchor) {
                d.fx = null;
                d.fy = null;
            }
        };
        return d3.drag()
            .filter(event => !event.target.closest('.node').classList.contains('faded'))
            .on("start", dragstarted)
            .on("drag", dragged)
            .on("end", dragended);
    }
    
    _calculateLinkPath(d) {
        const targetRadius = this._getNodeRadius(d.target);
        if (d.groupSize <= 1) { // 直线
            const dx = d.target.x - d.source.x;
            const dy = d.target.y - d.source.y;
            const dist = Math.sqrt(dx * dx + dy * dy);
            if (dist === 0) return `M${d.source.x},${d.source.y}L${d.target.x},${d.target.y}`;
            const newTargetX = d.target.x - (dx / dist) * targetRadius;
            const newTargetY = d.target.y - (dy / dist) * targetRadius;
            return `M${d.source.x},${d.source.y}L${newTargetX},${newTargetY}`;
        } else { // 弧线
            const dx = d.target.x - d.source.x;
            const dy = d.target.y - d.source.y;
            const side = (d.groupIndex % 2 === 0) ? 1 : -1;
            const rank = Math.ceil(d.groupIndex / 2);
            let curvature = rank * 0.15 * side;
            if (d.source.id > d.target.id) curvature *= -1;
            const midX = (d.source.x + d.target.x) / 2;
            const midY = (d.source.y + d.target.y) / 2;
            const cx = midX - curvature * dy;
            const cy = midY + curvature * dx;
            const cdx = d.target.x - cx;
            const cdy = d.target.y - cy;
            const cDist = Math.sqrt(cdx * cdx + cdy * cdy);
            if (cDist === 0) return `M${d.source.x},${d.source.y}Q${cx},${cy} ${d.target.x},${d.target.y}`;
            const newTargetX = d.target.x - (cdx / cDist) * targetRadius;
            const newTargetY = d.target.y - (cdy / cDist) * targetRadius;
            return `M${d.source.x},${d.source.y}Q${cx},${cy} ${newTargetX},${newTargetY}`;
        }
    }
    
    _calculateLinkLabelTransform(d) {
        if (!d.source || !d.target) return "";
        let midX = (d.source.x + d.target.x) / 2;
        let midY = (d.source.y + d.target.y) / 2;
        if (d.groupSize > 1) {
            const dx = d.target.x - d.source.x;
            const dy = d.target.y - d.source.y;
            const side = (d.groupIndex % 2 === 0) ? 1 : -1;
            const rank = Math.ceil(d.groupIndex / 2);
            let curvature = rank * 0.15 * side;
            if (d.source.id > d.target.id) curvature *= -1;
            const cx = midX - curvature * dy;
            const cy = midY + curvature * dx;
            midX = 0.25 * d.source.x + 0.5 * cx + 0.25 * d.target.x;
            midY = 0.25 * d.source.y + 0.5 * cy + 0.25 * d.target.y;
        }
        return `translate(${midX}, ${midY})`;
    }

    _handleNodeMouseover(event, d) {
        if (event.currentTarget.classList.contains('faded')) return;
        this.tooltip.style("opacity", 1).html(`<strong>ID:</strong> ${d.id}<br><strong>Type:</strong> ${d.type}<br><strong>度 (Degree):</strong> ${d.degree || 0}<br><strong>Desc:</strong> ${d.properties?.description || 'N/A'}`);
    }

    _handleLinkMouseover(event, d) {
        if (event.currentTarget.classList.contains('faded')) return;
        this.tooltip.style("opacity", 1).html(`<strong>Type:</strong> ${d.type}<br><strong>From:</strong> ${d.source.id}<br><strong>To:</strong> ${d.target.id}<br><strong>Desc:</strong> ${d.properties?.description || 'N/A'}`);
    }

    _handleMousemove(event) {
        this.tooltip.style("left", (event.pageX + 10) + "px").style("top", (event.pageY + 10) + "px");
    }

    _handleMouseout() {
        this.tooltip.style("opacity", 0);
    }
}
