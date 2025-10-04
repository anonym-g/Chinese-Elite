// docs/modules/graphView.js
import CONFIG from './config.js';
import { rgbToHex } from './utils.js';

/**
 * GraphView 类负责所有与D3.js和SVG相关的渲染任务。
 */
export class GraphView {
    constructor(selector, callbacks) {
        this.svg = d3.select(selector);
        this.width = this.svg.node().getBoundingClientRect().width;
        this.height = this.svg.node().getBoundingClientRect().height;
        this.tooltip = d3.select(".tooltip");
        
        this.callbacks = callbacks;
        
        this.container = this.svg.append("g");
        this.linkGroup = this.container.append("g").attr("class", "links");
        this.linkLabelGroup = this.container.append("g").attr("class", "link-labels");
        this.nodeGroup = this.container.append("g").attr("class", "nodes");
        
        this.colorScale = d3.scaleOrdinal();
        this.zoom = d3.zoom().on("zoom", (event) => this.container.attr("transform", event.transform));
        
        this.simulation = this._createSimulation();
        this._setupDefsAndZoom();

        this.svg.call(this.zoom).on("click", (e) => this.callbacks.onSvgClick(e));
        this.animationSourceNode = null;

        this.isInitialRender = true;
        // 为本次加载随机选择一个起始象限 (0:右上, 1:左上, 2:左下, 3:右下)
        this.initialQuadrant = Math.floor(Math.random() * 4);

        d3.select(".legend-toggle").on("click", () => {
            document.getElementById('legend-container').classList.toggle("collapsed");
        });
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
    
    render(graphData) {
        const { visibleNodes, validRels } = graphData;
        
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
            .attr("marker-end", d => !CONFIG.NON_DIRECTED_LINK_TYPES.has(d.type) ? "url(#arrowhead)" : null);

        const linkLabelUnits = this.linkLabelGroup.selectAll("g.link-label-unit")
            .data(validRels, d => `${d.source.id}-${d.target.id}-${d.type}`)
            .join(
                enter => {
                    const g = enter.append("g").attr("class", "link-label-unit");
                    g.append("rect");
                    g.append("text").attr("class", "link-label").text(d => d.type);
                    return g;
                }
            );

        linkLabelUnits
            .on("mouseover", this._handleLinkMouseover.bind(this))
            .on("mousemove", this._handleMousemove.bind(this))
            .on("mouseout", this._handleMouseout.bind(this));
        
        linkLabelUnits.each(function() {
            const textNode = d3.select(this).select("text").node();
            try {
                const bbox = textNode.getBBox();
                const padding = 2;
                d3.select(this).select("rect")
                    .attr("x", bbox.x - padding)
                    .attr("y", bbox.y - padding)
                    .attr("width", bbox.width + (padding * 2))
                    .attr("height", bbox.height + (padding * 2))
                    .attr("rx", 2)
                    .attr("ry", 2);
            } catch (e) {
                console.error("Could not get BBox for text label", textNode.textContent, e);
            }
        });

        const nodeElements = this.nodeGroup.selectAll("g.node")
            .data(visibleNodes, d => d.id)
            .join(
                enter => {
                    const g = enter.append("g").attr("class", "node");
                    const source = this.animationSourceNode;

                    enter.each(d => {
                        if (d.x === undefined) {
                            if (source) {
                                // 如果是点击展开，节点初始位置在源节点处
                                d.x = source.x;
                                d.y = source.y;
                            }
                            else {
                                // 如果是首次加载，则从选定的统一象限外、远距离入场
                                const quadrantAngleMap = [
                                    -Math.PI / 2, // 0: 右上 (TR)
                                    Math.PI / 2,  // 1: 左下 (BL) - 调整顺序以匹配视觉象限
                                    Math.PI,      // 2: 左上 (TL)
                                    0,            // 3: 右下 (BR)
                                ];
                                const baseAngle = quadrantAngleMap[this.initialQuadrant];
                                const randomAngleInQuadrant = baseAngle + (Math.random() - 0.5) * (Math.PI / 2);

                                // 将初始距离调得更远，确保节点和连线初始时都在屏幕外
                                const radius = Math.max(this.width, this.height);
                                const distance = radius * 1.5 + Math.random() * radius * 0.5;

                                d.x = this.width / 2 + distance * Math.cos(randomAngleInQuadrant);
                                d.y = this.height / 2 + distance * Math.sin(randomAngleInQuadrant);
                            }
                        }
                    });
                    
                    g.attr("transform", d => `translate(${d.x},${d.y})`);

                    g.append("circle")
                        .attr("r", 0)
                        .on("click", (e, d) => this.callbacks.onNodeClick(e, d))
                        .attr("fill", d => this.colorScale(d.type))
                        .transition()
                        .duration(750)
                        .attr("r", d => this._getNodeRadius(d));

                    g.append("text")
                        .attr("dy", ".3em")
                        .text(d => d?.name?.['zh-cn']?.[0] || d.id)
                        .style("opacity", 0)
                        .transition()
                        .delay(200)
                        .duration(500)
                        .style("opacity", 1);
                    
                    return g;
                },
                update => {
                    update.select("circle")
                        .transition().duration(200)
                        .attr("r", d => this._getNodeRadius(d))
                        .attr("fill", d => this.colorScale(d.type));
                    update.select("text").text(d => d?.name?.['zh-cn']?.[0] || d.id);
                    return update;
                },
                exit => exit.remove()
            )
            .call(this._createDragHandler());
            
        // 重置动画起始节点，以免影响后续不相关的操作
        if (this.animationSourceNode) {
            this.animationSourceNode = null;
        }
        
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

        // 根据是否为首次渲染，选择不同的alpha值
        const alphaValue = this.isInitialRender ? CONFIG.SIMULATION.INITIAL_ALPHA : CONFIG.SIMULATION.REHEAT_ALPHA;
        this.simulation.alpha(alphaValue).restart();

        if (this.isInitialRender) {
            this.isInitialRender = false;
        }
    }

    setInitialView() {
        const scale = CONFIG.INITIAL_ZOOM;
        const duration = CONFIG.INITIAL_ZOOM_DURATION;

        // 计算正确的平移量，使画布中心点(width/2, height/2)
        // 在缩放后，能够对齐屏幕的中心点(width/2, height/2)。
        const translateX = (this.width / 2) * (1 - scale);
        const translateY = (this.height / 2) * (1 - scale);

        // 创建一个包含正确平移和缩放的变换
        const transform = d3.zoomIdentity
            .translate(translateX, translateY)
            .scale(scale);

        // 将此变换平滑地应用到SVG上
        this.svg.transition()
            .duration(duration)
            .call(this.zoom.transform, transform);
    }

    setAnimationSource(sourceNode) {
        this.animationSourceNode = sourceNode;
    }

    _animateInNewNeighbor(data, sourceNode) {
        const { node, links } = data;
        const allNodes = this.simulation.nodes();
        const allLinks = this.simulation.force("link").links();

        if (node && !allNodes.find(n => n.id === node.id)) {
            node.x = sourceNode.x;
            node.y = sourceNode.y;
            
            const degreeCount = {};
            [...allLinks, ...links].forEach(rel => {
                const sourceId = (rel.source.id || rel.source);
                const targetId = (rel.target.id || rel.target);
                if (sourceId) degreeCount[sourceId] = (degreeCount[sourceId] || 0) + 1;
                if (targetId) degreeCount[targetId] = (degreeCount[targetId] || 0) + 1;
            });
            allNodes.forEach(n => n.degree = degreeCount[n.id] || n.degree || 0);
            node.degree = degreeCount[node.id] || 0;

            allNodes.push(node);
        }

        links.forEach(link => {
            link.source = allNodes.find(n => n.id === (link.source.id || link.source));
            link.target = allNodes.find(n => n.id === (link.target.id || link.target));
            if (link.source && link.target && !allLinks.find(l => l.source.id === link.source.id && l.target.id === link.target.id && l.type === link.type)) {
                allLinks.push(link);
            }
        });

        // 增量更新视图
        this.simulation.nodes(allNodes);
        this.simulation.force("link").links(allLinks);
        this.nodeGroup.selectAll("g.node")
            .data(allNodes, d => d.id)
            .join(
                enter => {
                    const g = enter.append("g").attr("class", "node");
                    g.attr("transform", d => `translate(${d.x},${d.y})`);
                    g.append("circle")
                        .attr("r", 0)
                        .attr("fill", d => this.colorScale(d.type))
                        .on("click", (e, d) => this.callbacks.onNodeClick(e, d))
                        .transition()
                        .duration(750)
                        .attr("r", d => this._getNodeRadius(d));
                    g.append("text")
                        .attr("dy", ".3em")
                        .text(d => d?.name?.['zh-cn']?.[0] || d.id);
                    return g;
                }
            )
            .call(this._createDragHandler())
            .on("mouseover", this._handleNodeMouseover.bind(this))
            .on("mousemove", this._handleMousemove.bind(this))
            .on("mouseout", this._handleMouseout.bind(this));

        this.simulation.alpha(0.3).restart();
    }

    updateHighlights(selectedNodeId, neighbors) {
        this.nodeGroup.selectAll('.node')
            .classed('path-highlight', false)
            .classed('path-source', false)
            .classed('path-target', false)
            .select('circle').style('stroke', null);

        this.linkGroup.selectAll('.link')
            .classed('path-highlight', false)
            .style('stroke', null);

        const isHighlighting = selectedNodeId !== null;

        const activeNodeIds = isHighlighting ? new Set([selectedNodeId, ...(neighbors[selectedNodeId] || [])]) : new Set();

        this.nodeGroup.selectAll('.node')
            .classed('faded', isHighlighting && (d => !activeNodeIds.has(d.id)))
            .classed('highlight', d => d.id === selectedNodeId);
        
        this.linkGroup.selectAll('.link')
            .classed('faded', isHighlighting && (d => !(d.source.id === selectedNodeId || d.target.id === selectedNodeId)))
            .style('stroke', d => {
                const isRelated = isHighlighting && (d.source.id === selectedNodeId || d.target.id === selectedNodeId);
                if (isRelated) {
                    const selectedNode = this.simulation.nodes().find(n => n.id === selectedNodeId);
                    return selectedNode ? this.colorScale(selectedNode.type) : CONFIG.COLORS.DEFAULT_LINK;
                }
                return null;
            });

        this.linkLabelGroup.selectAll('.link-label-unit')
            .classed('faded', isHighlighting && (d => !(d.source.id === selectedNodeId || d.target.id === selectedNodeId)));

        const selectedNode = isHighlighting ? this.simulation.nodes().find(n => n.id === selectedNodeId) : null;
        const highlightColor = selectedNode ? this.colorScale(selectedNode.type) : CONFIG.COLORS.DEFAULT_ARROW;
        this.svg.select("#arrowhead path").style('fill', highlightColor);
    }
    
    highlightPaths(paths, sourceId, targetId, sourceNode) {
        this.clearAllHighlights();
        
        const highlightColor = this.colorScale(sourceNode ? sourceNode.type : 'default');

        this.nodeGroup.selectAll(".node").classed("faded", true);
        this.linkGroup.selectAll(".link").classed("faded", true);
        this.linkLabelGroup.selectAll(".link-label-unit").classed("faded", true);
        
        paths.forEach((path, i) => {
            setTimeout(() => {
                this.nodeGroup.selectAll(".node.path-highlight").classed('path-highlight path-source path-target', false).select('circle').style('stroke', null);
                this.linkGroup.selectAll(".link.path-highlight").classed('path-highlight', false).style('stroke', null);
                
                const pathNodeIds = new Set(path);
                
                this.nodeGroup.selectAll('.node')
                    .filter(d => pathNodeIds.has(d.id))
                    .classed('faded', false)
                    .classed('path-highlight', true)
                    .classed('path-source', d => d.id === sourceId)
                    .classed('path-target', d => d.id === targetId)
                    .select('circle').style('stroke', highlightColor);
                
                for (let j = 0; j < path.length - 1; j++) {
                    const source = path[j];
                    const target = path[j + 1];

                    this.linkGroup.selectAll('.link')
                        .filter(d => (d.source.id === source && d.target.id === target) || (d.source.id === target && d.target.id === source))
                        .classed('faded', false)
                        .classed('path-highlight', true)
                        .style('stroke', highlightColor);
                    
                    this.linkLabelGroup.selectAll('.link-label-unit')
                        .filter(d => (d.source.id === source && d.target.id === target) || (d.source.id === target && d.target.id === source))
                        .classed('faded', false);
                }
                
                this.svg.select("#arrowhead path").style('fill', highlightColor);
            }, i * 2000);
        });
    }

    clearAllHighlights() {
        this.nodeGroup.selectAll(".node").classed("faded", false).classed("highlight", false).classed('path-highlight path-source path-target', false).select('circle').style('stroke', null);
        this.linkGroup.selectAll(".link").classed("faded", false).classed('path-highlight', false).style("stroke", null);
        this.linkLabelGroup.selectAll(".link-label-unit").classed("faded", false);
        this.svg.select("#arrowhead path").style('fill', CONFIG.COLORS.DEFAULT_ARROW);
    }

    dimGraph() {
        this.container.transition().duration(500).style("opacity", 0.15);
    }

    undimGraph() {
        this.container.transition().duration(500).style("opacity", 1);
    }

    getCenterOfView() {
        const transform = d3.zoomTransform(this.svg.node());
        // 将屏幕中心点坐标转换为SVG画布内的坐标
        return {
            x: (this.width / 2 - transform.x) / transform.k,
            y: (this.height / 2 - transform.y) / transform.k,
        };
    }

    centerOnNode(nodeData, scale = 1.5, duration = 750) {
        if(!nodeData?.x || !nodeData?.y) return;
        const x = this.width / 2 - nodeData.x * scale;
        const y = this.height / 2 - nodeData.y * scale;
        this.svg.transition().duration(duration).call(this.zoom.transform, d3.zoomIdentity.translate(x, y).scale(scale));
    }

    _createSimulation() {
        const simulation = d3.forceSimulation()
            .force("link", d3.forceLink().id(d => d.id).distance(CONFIG.SIMULATION.LINK_DISTANCE))
            .force("charge", d3.forceManyBody().strength(CONFIG.SIMULATION.CHARGE_STRENGTH))
            .force("x", d3.forceX(this.width / 2).strength(d => d.isAnchor ? CONFIG.SIMULATION.ANCHOR_STRENGTH : CONFIG.SIMULATION.CENTER_X_STRENGTH))
            .force("y", d3.forceY(this.height / 2).strength(d => d.isAnchor ? CONFIG.SIMULATION.ANCHOR_STRENGTH : CONFIG.SIMULATION.CENTER_Y_STRENGTH));
        
        simulation.on("tick", this._handleTick.bind(this));
        return simulation;
    }

    _setupDefsAndZoom() {
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
            
        this.zoom.on("zoom", (event) => {
            this.container.attr("transform", event.transform);
            if (event.sourceEvent) {
                if (this.tooltip.style("opacity") === "1") {
                    this.tooltip.style("left", (event.sourceEvent.pageX + 10) + "px")
                               .style("top", (event.sourceEvent.pageY + 10) + "px");
                }
            }
        });

        this.svg.call(this.zoom).on("click", (e) => this.callbacks.onSvgClick(e));
    }
    
    _handleTick() {
        this.linkGroup.selectAll("path.link").attr("d", d => this._calculateLinkPath(d));
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
        if (d.groupSize <= 1) {
            const dx = d.target.x - d.source.x;
            const dy = d.target.y - d.source.y;
            const dist = Math.sqrt(dx * dx + dy * dy);
            if (dist === 0) return `M${d.source.x},${d.source.y}L${d.target.x},${d.target.y}`;
            const newTargetX = d.target.x - (dx / dist) * targetRadius;
            const newTargetY = d.target.y - (dy / dist) * targetRadius;
            return `M${d.source.x},${d.source.y}L${newTargetX},${newTargetY}`;
        }
        else {
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
        const displayName = d?.name?.['zh-cn']?.[0] || d.id;
        this.tooltip.style("opacity", 1).html(
            `<strong>名称:</strong> ${displayName}<br>` +
            `<strong>ID:</strong> ${d.id}<br>` +
            `<strong>Type:</strong> ${d.type}<br>` +
            `<strong>度 (Degree):</strong> ${d.degree || 0}<br>` +
            `<strong>Desc:</strong> ${d.properties?.description || 'N/A'}`
        );
    }

    _handleLinkMouseover(event, d) {
        if (event.currentTarget.classList.contains('faded')) return;
        const sourceName = d.source?.name?.['zh-cn']?.[0] || d.source.id;
        const targetName = d.target?.name?.['zh-cn']?.[0] || d.target.id;
        this.tooltip.style("opacity", 1).html(
            `${sourceName} ${d.type} ${targetName}<br>` +
            // `<strong>From:</strong> ${sourceName}<br>` +
            // `<strong>To:</strong> ${targetName}<br>` +
            `<strong>Desc:</strong> ${d.properties?.description || 'N/A'}`
        );
    }

    _handleMousemove(event) {
        this.tooltip.style("left", (event.pageX + 10) + "px").style("top", (event.pageY + 10) + "px");
    }

    _handleMouseout() {
        this.tooltip.style("opacity", 0);
    }
}
