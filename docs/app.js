// docs/app.js

document.addEventListener('DOMContentLoaded', () => {
    const graph = new InteractiveGraph('#graph');
    graph.initialize();
});

// --- 全局配置 ---
const CONFIG = {
    DATA_FILE_URL: './consolidated_graph.json',
    SIMULATION: {
        CHARGE_STRENGTH: -250,
        LINK_DISTANCE: 100,
        CENTER_X_STRENGTH: 0.01,
        CENTER_Y_STRENGTH: 0.01,
        ANCHOR_STRENGTH: 0.3
    },
    NODE_RADIUS: {
        BASE: 5,
        SCALE: 2
    },
    COLORS: {
        DEFAULT_LINK: '#fff',
        DEFAULT_ARROW: '#999',
        NODE_TYPES: {
           'Movement': 'rgb(0, 255, 255)',
           'Person': 'rgb(255, 0, 0)',
           'Organization': 'rgb(25, 40, 200)',
           'Event': 'rgb(170, 30, 170)',
           'Location': 'rgb(225, 170, 30)',
           'Document': 'rgb(15, 255, 50)'
        }
    },
    NON_DIRECTED_LINK_TYPES: new Set(['SIBLING_OF', 'LOVER_OF', 'SEXUAL_REL', 'RELATIVE_OF', 'FRIEND_OF', 'ENEMY_OF', 'MET_WITH'])
};


/**
 * 主应用类，封装了图谱的所有逻辑
 */
class InteractiveGraph {
    constructor(selector) {
        // --- D3 选择集 ---
        this.svg = d3.select(selector);
        this.width = +this.svg.node().getBoundingClientRect().width;
        this.height = +this.svg.node().getBoundingClientRect().height;
        this.tooltip = d3.select(".tooltip");
        this.container = this.svg.append("g");
        this.linkGroup = this.container.append("g").attr("class", "links");
        this.linkLabelGroup = this.container.append("g").attr("class", "link-labels");
        this.nodeGroup = this.container.append("g").attr("class", "nodes");

        // --- 核心数据和状态 ---
        this.fullGraphData = { nodes: [], relationships: [] };
        this.state = {
            startDate: getDateFromGroup('start'),
            endDate: getDateFromGroup('end'),
            hiddenTypes: new Set(),
            selectedNodeId: null,
            isIntervalLocked: false,
            currentInterval: { years: 0, months: 0, days: 0 }
        };
        this.neighbors = {};
        this.colorScale = d3.scaleOrdinal(d3.schemeCategory10);
        
        this.simulation = this._createSimulation();
        this.debouncedRender = this._debounce(this.render.bind(this), 300);
    }

    /**
     * 初始化整个应用
     */
    async initialize() {
        this._setupDefsAndZoom();
        this._setupEventListeners();
        try {
            this.fullGraphData = await this._loadData();
            this._createLegend();
            this.render();
        } catch (error) {
            console.error('Failed to initialize graph:', error);
            d3.select("#graph-container").html(`<h2>错误：无法加载关系图谱数据。</h2><p>详细错误: <code>${error.message}</code></p>`);
        }
    }

    // ===================================================================
    // 渲染核心流程
    // ===================================================================

    /**
     * 主渲染函数
     */
    render() {
        this.state.startDate = getDateFromGroup('start');
        this.state.endDate = getDateFromGroup('end');

        const { visibleNodes, validRels } = this._applyFilters();
        this._buildNeighborMap(validRels);
        
        this._updateDOM(visibleNodes, validRels);
        this._updateSimulation(visibleNodes, validRels);
        this._updateHighlights();
    }
    
    /**
     * 步骤1: 根据 state 筛选数据
     */
    _applyFilters() {
        const { startDate, endDate, hiddenTypes } = this.state;
        
        const effectiveStartDate = startDate || new Date(-8640000000000000);
        const effectiveEndDate = endDate ? new Date(endDate.getTime() + 24 * 60 * 60 * 1000 - 1) : new Date(8640000000000000);

        if (startDate && endDate && endDate < startDate) {
             return { visibleNodes: [], validRels: [] };
        }

        const timeFilteredRels = this.fullGraphData.relationships.filter(rel => this._isRelActive(rel, effectiveStartDate, effectiveEndDate));
        
        const activeNodeIds = new Set();
        timeFilteredRels.forEach(rel => {
            if (rel.source) activeNodeIds.add(rel.source);
            if (rel.target) activeNodeIds.add(rel.target);
        });
        
        // 使用两步过滤，确保逻辑清晰
        const connectedNodes = this.fullGraphData.nodes.filter(node => node && activeNodeIds.has(node.id));
        const timeFilteredNodes = connectedNodes.filter(node => this._isNodeActive(node, effectiveStartDate, effectiveEndDate));

        const degreeCount = {};
        timeFilteredRels.forEach(rel => {
            if (rel.source) degreeCount[rel.source] = (degreeCount[rel.source] || 0) + 1;
            if (rel.target) degreeCount[rel.target] = (degreeCount[rel.target] || 0) + 1;
        });
        timeFilteredNodes.forEach(node => {
            if (node) node.degree = degreeCount[node.id] || 0;
        });

        const visibleNodes = timeFilteredNodes.filter(node => node && !hiddenTypes.has(node.type));
        const visibleNodeIds = new Set(visibleNodes.map(n => n.id));
        
        const visibleRels = timeFilteredRels.filter(rel => rel && visibleNodeIds.has(rel.source) && visibleNodeIds.has(rel.target));
        const nodeById = new Map(visibleNodes.map(node => [node.id, node]));

        const validRels = visibleRels.map(link => ({
            ...link,
            source: nodeById.get(link.source),
            target: nodeById.get(link.target)
        })).filter(link => link.source && link.target);

        return { visibleNodes, validRels };
    }

    /**
     * 步骤2: 更新 D3 DOM 元素
     */
    _updateDOM(visibleNodes, validRels) {
        visibleNodes.forEach(n => n.isAnchor = false);
        [...visibleNodes].sort((a, b) => b.degree - a.degree).slice(0, 3).forEach(n => n.isAnchor = true);

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
        
        this.linkGroup.selectAll("path.link")
            .data(validRels, d => `${d.source.id}-${d.target.id}-${d.type}`)
            .join("path")
            .attr("class", "link")
            .attr("marker-end", d => !CONFIG.NON_DIRECTED_LINK_TYPES.has(d.type) ? "url(#arrowhead)" : null)
            .on("mouseover", this._handleLinkMouseover.bind(this))
            .on("mousemove", this._handleMousemove.bind(this))
            .on("mouseout", this._handleMouseout.bind(this));

        this.linkLabelGroup.selectAll("text.link-label")
            .data(validRels, d => `${d.source.id}-${d.target.id}-${d.type}`)
            .join("text")
            .attr("class", "link-label")
            .text(d => d.type);
            
        const nodeElements = this.nodeGroup.selectAll("g.node")
            .data(visibleNodes, d => d.id)
            .join(enter => {
                const g = enter.append("g").attr("class", "node");
                g.append("circle")
                  .on("click", (e, d) => this._handleNodeClick(e, d));
                g.append("text").attr("dy", ".3em").text(d => d.id);
                return g;
            })
            .call(this._createDragHandler());

        nodeElements.select("circle")
            .transition().duration(200)
            .attr("r", d => this._getNodeRadius(d))
            .attr("fill", d => this.colorScale(d.type));
            
        nodeElements
            .on("mouseover", this._handleNodeMouseover.bind(this))
            .on("mousemove", this._handleMousemove.bind(this))
            .on("mouseout", this._handleMouseout.bind(this));
    }

    /**
     * 步骤3: 更新力导向模拟
     */
    _updateSimulation(visibleNodes, validRels) {
        this.simulation.nodes(visibleNodes);
        this.simulation.force("link").links(validRels);
        this.simulation.alpha(0.3).restart();
    }

    // ===================================================================
    // 初始化和设置
    // ===================================================================

    _createSimulation() {
        const simulation = d3.forceSimulation()
            .force("link", d3.forceLink().id(d => d.id).distance(CONFIG.SIMULATION.LINK_DISTANCE))
            .force("charge", d3.forceManyBody().strength(CONFIG.SIMULATION.CHARGE_STRENGTH))
            .force("x", d3.forceX(this.width / 2).strength(d => d.isAnchor ? CONFIG.SIMULATION.ANCHOR_STRENGTH : CONFIG.SIMULATION.CENTER_X_STRENGTH))
            .force("y", d3.forceY(this.height / 2).strength(d => d.isAnchor ? CONFIG.SIMULATION.ANCHOR_STRENGTH : CONFIG.SIMULATION.CENTER_Y_STRENGTH));

        simulation.on("tick", this._handleTick.bind(this));
        return simulation;
    }

    async _loadData() {
        const response = await fetch(CONFIG.DATA_FILE_URL);
        if (!response.ok) throw new Error(`Network response was not ok. Status: ${response.status}`);
        const data = await response.json();
        if (!data) throw new Error("Loaded data is null or empty.");
        return data;
    }

    _setupDefsAndZoom() {
        this.svg.append('defs').append('marker')
            .attr('id', 'arrowhead')
            .attr('viewBox', '-10 -5 10 10')
            .attr('refX', 0).attr('refY', 0)
            .attr('orient', 'auto')
            .attr('markerWidth', 8).attr('markerHeight', 8)
            .attr('xoverflow', 'visible')
            .append('svg:path')
            .attr('d', 'M -10,-5 L 0,0 L -10,5')
            .attr('class', 'arrowhead-path')
            .style('fill', CONFIG.COLORS.DEFAULT_ARROW);

        const zoom = d3.zoom().on("zoom", (event) => this.container.attr("transform", event.transform));
        this.svg.call(zoom).on("click", this._handleSvgClick.bind(this));
    }

    _createLegend() {
        const nodeTypes = [...new Set(this.fullGraphData.nodes.map(n => n.type))];
        const colorRange = nodeTypes.map(type => CONFIG.COLORS.NODE_TYPES[type] || null);
        this.colorScale.domain(nodeTypes).range(colorRange.filter(c => c)).unknown("#cccccc");

        const legendContent = d3.select("#legend-container .legend-content");
        legendContent.selectAll("*").remove();

        // 记录初始高度
        const container = document.getElementById('legend-container');
        const originalHeight = container.offsetHeight;
        container.style.height = originalHeight + 'px';

        // 添加切换按钮事件
        d3.select(".legend-toggle").on("click", () => {
            container.classList.toggle("collapsed");
        });

        nodeTypes.forEach(type => {
            const item = legendContent.append("div").attr("class", "legend-item");
            const colorBox = item.append("div")
                .attr("class", "color-box")
                .style("background-color", this.colorScale(type));
            
            item.append("input")
                .attr("type", "checkbox")
                .attr("checked", true)
                .on("change", (e) => this._handleLegendToggle(e, type, colorBox));
            
            item.append("span").text(type);

            item.append("input")
                .attr("type", "color")
                .attr("value", rgbToHex(this.colorScale(type)))
                .on("input", this._debounce((event) => {
                    const newColor = event.target.value;
                    const newRange = this.colorScale.range();
                    const typeIndex = this.colorScale.domain().indexOf(type);
                    if (typeIndex > -1) {
                        newRange[typeIndex] = newColor;
                        this.colorScale.range(newRange);
                    }
                    colorBox.style("background-color", newColor);
                    this.render();
                }, 100));
        });
    }

    // ===================================================================
    // 事件处理器
    // ===================================================================

    _modifyDate(prefix, part, direction) {
        const currentDate = getDateFromGroup(prefix) || new Date();
        if (isNaN(currentDate.getTime())) return;

        const targetInput = document.getElementById(`${prefix}-${part}`);
        if (targetInput.value === '') {
            updateDateGroup(prefix, currentDate);
        }

        switch (part) {
            case 'year':  currentDate.setFullYear(currentDate.getFullYear() + direction); break;
            case 'month': currentDate.setMonth(currentDate.getMonth() + direction); break;
            case 'day':   currentDate.setDate(currentDate.getDate() + direction); break;
        }
        
        updateDateGroup(prefix, currentDate);

        if (this.state.isIntervalLocked) {
            this._propagateIntervalChange(prefix);
        }
        this.debouncedRender();
    }
    
    _setupEventListeners() {
        document.querySelectorAll('.date-part').forEach(input => {
            input.addEventListener('keydown', (e) => this._handleDatePartKeydown(e));
            
            input.addEventListener('wheel', (e) => {
                e.preventDefault();
                const direction = e.deltaY < 0 ? 1 : -1;
                const [prefix, part] = e.target.id.split('-');
                this._modifyDate(prefix, part, direction);
            });

            input.addEventListener('input', (e) => {
                e.target.value = e.target.value.replace(/[^0-9]/g, '');
                if (this.state.isIntervalLocked) {
                    this._propagateIntervalChange(e.target.id.split('-')[0]);
                }
                this.debouncedRender();
            });
        });

        document.querySelector('.controls').addEventListener('click', (e) => {
            if (e.target.matches('.arrow')) {
                const targetInputId = e.target.dataset.target;
                const direction = e.target.classList.contains('up') ? 1 : -1;
                const [prefix, part] = targetInputId.split('-');
                this._modifyDate(prefix, part, direction);
            }
        });

        document.getElementById('toggle-interval-btn').addEventListener('click', e => {
            e.preventDefault();
            document.getElementById('interval-inputs-wrapper').classList.toggle('hidden');
        });
        document.getElementById('set-interval-btn').addEventListener('click', () => this._handleSetInterval());
        document.getElementById('clear-interval-btn').addEventListener('click', () => this._handleClearInterval());
    }

    _handleDatePartKeydown(event) {
        const target = event.target;
        const [prefix, part] = target.id.split('-');
        
        const isAtStart = target.selectionStart === 0 && target.selectionEnd === 0;
        const isAtEnd = target.selectionStart === target.value.length && target.selectionEnd === target.value.length;

        if (event.key === 'ArrowLeft' && isAtStart) {
            const prevInput = getAdjacentInput(prefix, part, 'prev');
            if (prevInput) { event.preventDefault(); prevInput.focus(); prevInput.setSelectionRange(prevInput.value.length, prevInput.value.length); }
        } else if (event.key === 'ArrowRight' && isAtEnd) {
            const nextInput = getAdjacentInput(prefix, part, 'next');
            if (nextInput) { event.preventDefault(); nextInput.focus(); }
        }

        if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
            event.preventDefault();
            const direction = (event.key === 'ArrowUp') ? 1 : -1;
            this._modifyDate(prefix, part, direction);
        }
    }

    _handleTick() {
        this.linkGroup.selectAll("path.link").attr("d", d => this._calculateLinkPath(d));
        this.linkLabelGroup.selectAll("text.link-label").attr("transform", d => this._calculateLinkLabelTransform(d));
        this.nodeGroup.selectAll("g.node").attr("transform", d => `translate(${d.x},${d.y})`);
    }

    _handleNodeClick(event, d) {
        event.stopPropagation();
        this.state.selectedNodeId = (this.state.selectedNodeId === d.id) ? null : d.id;
        this._updateHighlights();
    }
    
    _handleSvgClick() {
        this.state.selectedNodeId = null;
        this._updateHighlights();
    }
    
    _handleLegendToggle(event, type, colorBox) {
        if (event.target.checked) {
            this.state.hiddenTypes.delete(type);
            colorBox.style("background-color", this.colorScale(type));
        } else {
            this.state.hiddenTypes.add(type);
            colorBox.style("background-color", "#ccc");
        }
        this.render();
    }

    _updateHighlights() {
        const { selectedNodeId } = this.state;
        const isHighlighting = selectedNodeId !== null;
        
        let highlightColor = null;
        if (isHighlighting) {
            const selectedNode = this.fullGraphData.nodes.find(n => n.id === selectedNodeId);
            highlightColor = this.colorScale(selectedNode?.type);
        }
        
        const nodeIsFaded = n => isHighlighting && n.id !== selectedNodeId && !this.neighbors[selectedNodeId]?.includes(n.id);
        const linkIsRelated = l => isHighlighting && (l.source.id === selectedNodeId || l.target.id === selectedNodeId);

        this.nodeGroup.selectAll(".node")
            .classed("faded", d => nodeIsFaded(d))
            .classed("highlight", d => isHighlighting && d.id === selectedNodeId);

        this.linkGroup.selectAll(".link")
            .classed("faded", d => isHighlighting && !linkIsRelated(d))
            .style("stroke", d => linkIsRelated(d) ? highlightColor : CONFIG.COLORS.DEFAULT_LINK)
            .style("stroke-opacity", d => linkIsRelated(d) ? 1.0 : 0.6);
            
        this.linkLabelGroup.selectAll(".link-label")
            .classed("faded", d => isHighlighting && !linkIsRelated(d));

        this.svg.select("#arrowhead path")
            .style('fill', isHighlighting ? highlightColor : CONFIG.COLORS.DEFAULT_ARROW);
    }
    
    _handleSetInterval() {
        const years = parseInt(document.getElementById('interval-year').value) || 0;
        const months = parseInt(document.getElementById('interval-month').value) || 0;
        const days = parseInt(document.getElementById('interval-day').value) || 0;
        this.state.currentInterval = { years, months, days };
        this.state.isIntervalLocked = true;
        this._propagateIntervalChange('start');
        this.render();
        document.getElementById('clear-interval-btn').classList.remove('hidden');
        document.getElementById('interval-inputs-wrapper').classList.add('hidden');
    }

    _handleClearInterval() {
        this.state.isIntervalLocked = false;
        this.state.currentInterval = { years: 0, months: 0, days: 0 };
        document.getElementById('clear-interval-btn').classList.add('hidden');
    }

    _handleNodeMouseover(event, d) {
        this.tooltip.style("opacity", 1)
           .html(`<strong>ID:</strong> ${d.id}<br><strong>Type:</strong> ${d.type}<br><strong>度 (Degree):</strong> ${d.degree || 0}<br><strong>Desc:</strong> ${d.properties?.description || 'N/A'}`);
    }

    _handleLinkMouseover(event, d) {
        this.tooltip.style("opacity", 1)
           .html(`<strong>Type:</strong> ${d.type}<br><strong>From:</strong> ${d.source.id}<br><strong>To:</strong> ${d.target.id}<br><strong>Desc:</strong> ${d.properties?.description || 'N/A'}`);
    }
    
    _handleMousemove(event) {
        this.tooltip.style("left", (event.pageX + 10) + "px").style("top", (event.pageY + 10) + "px");
    }

    _handleMouseout() {
        this.tooltip.style("opacity", 0);
    }
    
    // ===================================================================
    // 辅助/工具函数
    // ===================================================================
    
    _debounce(func, delay) {
        let timeout;
        return (...args) => {
            clearTimeout(timeout);
            timeout = setTimeout(() => func.apply(this, args), delay);
        };
    }

    _isRelActive(rel, start, end) {
        if (!rel?.properties?.start_date) return true;
        const startDates = Array.isArray(rel.properties.start_date) ? rel.properties.start_date : [rel.properties.start_date];
        const endDates = rel.properties.end_date ? (Array.isArray(rel.properties.end_date) ? rel.properties.end_date : [rel.properties.end_date]) : [];
        return startDates.some((startStr, i) => {
            const relStart = parseDate(startStr);
            if (!relStart) return false;
            const relEnd = endDates[i] ? parseDate(endDates[i]) : end;
            return relStart <= end && relEnd >= start;
        });
    }

    /**
     * 范围过滤逻辑
     */
    _isNodeActive(node, start, end) {
        if (node.type === 'Location') return true;

        let nodeDateProp = null;

        // 先检查Person&lifetime, 因LLM输出并不总是省略空值（person通常也具有空period，会导致渲染错误）
        if (node.type === 'Person' && node.properties?.lifetime) {
            nodeDateProp = node.properties.lifetime;
        } 
        else if (node.properties?.period) {
            nodeDateProp = node.properties.period;
        }

        if (!nodeDateProp) return true;

        const dateRanges = Array.isArray(nodeDateProp) ? nodeDateProp : [nodeDateProp];

        return dateRanges.some(rangeStr => {
            let startStr, endStr;

            // 解析日期范围字符串
            if (rangeStr.includes(' - ')) {
                const parts = rangeStr.split(' - ');
                startStr = parts[0]?.trim();
                endStr = parts[1]?.trim();
            } else if (rangeStr.trim().endsWith('-')) {
                startStr = rangeStr.trim().slice(0, -1).trim();
                endStr = '';
            } else if (rangeStr.trim().startsWith('-')) {
                startStr = '';
                endStr = rangeStr.trim().slice(1).trim();
            } else {
                startStr = rangeStr.trim();
                endStr = rangeStr.trim();
            }

            // 解析日期
            const parsedNodeStart = parseDate(startStr);
            const parsedNodeEnd = parseDate(endStr);
            
            // 如果提供了日期但解析失败，返回 false 而不是 true —— 便于debug
            if ((startStr && !parsedNodeStart) || (endStr && !parsedNodeEnd)) {
                return false;
            }

            // 设置日期范围边界
            const finalNodeStart = parsedNodeStart || new Date(-8640000000000000);
            const finalNodeEnd = parsedNodeEnd || new Date(8640000000000000);

            // 日期范围比较：节点的时间范围必须与查询时间范围有重叠
            return finalNodeStart <= end && finalNodeEnd >= start;
        });
    }

    _buildNeighborMap(validRels) {
        this.neighbors = {};
        validRels.forEach(d => {
            if (d.source && d.target) {
                if (!this.neighbors[d.source.id]) this.neighbors[d.source.id] = [];
                if (!this.neighbors[d.target.id]) this.neighbors[d.target.id] = [];
                this.neighbors[d.source.id].push(d.target.id);
                this.neighbors[d.target.id].push(d.source.id);
            }
        });
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
        return d3.drag().on("start", dragstarted).on("drag", dragged).on("end", dragended);
    }

    _calculateLinkPath(d) {
        const targetRadius = this._getNodeRadius(d.target);
        if (d.groupSize <= 1) {
            const dx = d.target.x - d.source.x, dy = d.target.y - d.source.y;
            const dist = Math.sqrt(dx * dx + dy * dy);
            if (dist === 0) return `M${d.source.x},${d.source.y}L${d.target.x},${d.target.y}`;
            const newTargetX = d.target.x - (dx / dist) * targetRadius;
            const newTargetY = d.target.y - (dy / dist) * targetRadius;
            return `M${d.source.x},${d.source.y}L${newTargetX},${newTargetY}`;
        } else {
            const dx = d.target.x - d.source.x, dy = d.target.y - d.source.y;
            const side = (d.groupIndex % 2 === 0) ? 1 : -1, rank = Math.ceil(d.groupIndex / 2);
            const curvature = rank * 0.15 * side;
            const midX = (d.source.x + d.target.x) / 2, midY = (d.source.y + d.target.y) / 2;
            const cx = midX - curvature * dy, cy = midY + curvature * dx;
            const cdx = d.target.x - cx, cdy = d.target.y - cy, cDist = Math.sqrt(cdx*cdx + cdy*cdy);
            if (cDist === 0) return `M${d.source.x},${d.source.y}Q${cx},${cy} ${d.target.x},${d.target.y}`;
            const newTargetX = d.target.x - (cdx / cDist) * targetRadius;
            const newTargetY = d.target.y - (cdy / cDist) * targetRadius;
            return `M${d.source.x},${d.source.y}Q${cx},${cy} ${newTargetX},${newTargetY}`;
        }
    }

    _calculateLinkLabelTransform(d) {
        if (!d.source || !d.target) return "";
        let midX = (d.source.x + d.target.x) / 2, midY = (d.source.y + d.target.y) / 2;
        if (d.groupSize > 1) {
            const dx = d.target.x - d.source.x, dy = d.target.y - d.source.y;
            const side = (d.groupIndex % 2 === 0) ? 1 : -1, rank = Math.ceil(d.groupIndex / 2);
            const curvature = rank * 0.15 * side;
            const cx = midX - curvature * dy, cy = midY + curvature * dx;
            midX = 0.25 * d.source.x + 0.5 * cx + 0.25 * d.target.x;
            midY = 0.25 * d.source.y + 0.5 * cy + 0.25 * d.target.y;
        }
        return `translate(${midX}, ${midY})`;
    }

    _propagateIntervalChange(originPrefix) {
        const originDate = getDateFromGroup(originPrefix);
        if (!originDate) return;
        if (originPrefix === 'start') {
            const newEndDate = addInterval(originDate, this.state.currentInterval);
            updateDateGroup('end', newEndDate);
        } else {
            const newStartDate = subtractInterval(originDate, this.state.currentInterval);
            updateDateGroup('start', newStartDate);
        }
    }
}


// --- 独立于类的纯工具函数 ---

function parseDate(dateStr) {
    if (!dateStr || typeof dateStr !== 'string') return null;
    if (/^\d{4}$/.test(dateStr)) return new Date(`${dateStr}-01-01T00:00:00`);
    if (/^\d{4}-\d{2}$/.test(dateStr)) return new Date(`${dateStr}-01T00:00:00`);
    const date = new Date(`${dateStr}T00:00:00`);
    return isNaN(date.getTime()) ? null : date;
}

function getDateFromGroup(prefix) {
    const year = document.getElementById(`${prefix}-year`).value;
    const month = document.getElementById(`${prefix}-month`).value;
    const day = document.getElementById(`${prefix}-day`).value;
    if (!year || !month || !day) return null;
    const date = new Date(year, month - 1, day);
    return (date.getFullYear() != year || date.getMonth() + 1 != month || date.getDate() != day) ? null : date;
}

function updateDateGroup(prefix, date) {
    document.getElementById(`${prefix}-year`).value = date.getFullYear();
    document.getElementById(`${prefix}-month`).value = String(date.getMonth() + 1).padStart(2, '0');
    document.getElementById(`${prefix}-day`).value = String(date.getDate()).padStart(2, '0');
}

function addInterval(date, interval) {
    const newDate = new Date(date);
    if (interval.years) newDate.setFullYear(newDate.getFullYear() + interval.years);
    if (interval.months) newDate.setMonth(newDate.getMonth() + interval.months);
    if (interval.days) newDate.setDate(newDate.getDate() + interval.days);
    return newDate;
}

function subtractInterval(date, interval) {
    const newDate = new Date(date);
    if (interval.days) newDate.setDate(newDate.getDate() - interval.days);
    if (interval.months) newDate.setMonth(newDate.getMonth() - interval.months);
    if (interval.years) newDate.setFullYear(newDate.getFullYear() - interval.years);
    return newDate;
}

function getAdjacentInput(prefix, part, direction) {
    const order = ['year', 'month', 'day'];
    const currentIndex = order.indexOf(part);
    const nextIndex = direction === 'next' ? currentIndex + 1 : currentIndex - 1;
    if (nextIndex >= 0 && nextIndex < order.length) {
        return document.getElementById(`${prefix}-${order[nextIndex]}`);
    }
    return null;
}

function rgbToHex(rgb) {
    if (!rgb || !rgb.startsWith('rgb')) return '#000000';
    const match = rgb.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
    if (!match) return '#000000';
    const toHex = (c) => ('0' + parseInt(c, 10).toString(16)).slice(-2);
    return `#${toHex(match[1])}${toHex(match[2])}${toHex(match[3])}`;
}