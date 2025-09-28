// docs/app.js --- FIXED TIME FILTERING LOGIC ---

document.addEventListener('DOMContentLoaded', initialize);

const DATA_FILE_URL = './consolidated_graph.json';

// --- 全局变量 ---
let fullGraphData = null;
let colorScale = d3.scaleOrdinal(d3.schemeCategory10);
const hiddenTypes = new Set();
let neighbors = {};
let isIntervalLocked = false;
let currentInterval = { years: 0, months: 0, days: 0 };


// --- D3 全局变量 (精简初始化) ---
const svg = d3.select("#graph");
const tooltip = d3.select(".tooltip");
const width = +svg.node().getBoundingClientRect().width;
const height = +svg.node().getBoundingClientRect().height;

const container = svg.append("g");
const linkGroup = container.append("g").attr("class", "links");
const linkLabelGroup = container.append("g").attr("class", "link-labels");
const nodeGroup = container.append("g").attr("class", "nodes");

const simulation = d3.forceSimulation()
    .force("link", d3.forceLink().id(d => d.id).distance(100))
    .force("charge", d3.forceManyBody().strength(-250))
    .force("x", d3.forceX(width / 2).strength(d => d.isAnchor ? 0.3 : 0.01))
    .force("y", d3.forceY(height / 2).strength(d => d.isAnchor ? 0.3 : 0.01));

// ===================================================================
// ===================  辅助函数 =====================================
// ===================================================================

function debounce(func, delay) {
    let timeout;
    return function(...args) {
        const context = this;
        clearTimeout(timeout);
        timeout = setTimeout(() => func.apply(context, args), delay);
    };
}

const nonDirectedTypes = new Set(['SIBLING_OF', 'LOVER_OF', 'SEXUAL_REL', 'RELATIVE_OF', 'FRIEND_OF', 'ENEMY_OF', 'MET_WITH']);

function getAdjacentInput(prefix, part, direction) {
    const order = ['year', 'month', 'day'];
    const currentIndex = order.indexOf(part);
    const nextIndex = direction === 'next' ? currentIndex + 1 : currentIndex - 1;
    if (nextIndex >= 0 && nextIndex < order.length) {
        return document.getElementById(`${prefix}-${order[nextIndex]}`);
    }
    return null;
}

function getDateFromGroup(prefix) {
    const year = document.getElementById(`${prefix}-year`).value;
    const month = document.getElementById(`${prefix}-month`).value;
    const day = document.getElementById(`${prefix}-day`).value;
    if (!year || !month || !day) return null;
    // Month is 0-indexed in JS Date
    const date = new Date(year, month - 1, day);
    // Basic validation to prevent invalid dates like Feb 30
    if (date.getFullYear() != year || date.getMonth() + 1 != month || date.getDate() != day) {
        return null;
    }
    return date;
}

function updateDateGroup(prefix, dateObject) {
    document.getElementById(`${prefix}-year`).value = dateObject.getFullYear();
    document.getElementById(`${prefix}-month`).value = String(dateObject.getMonth() + 1).padStart(2, '0');
    document.getElementById(`${prefix}-day`).value = String(dateObject.getDate()).padStart(2, '0');
}

function parseDate(dateStr) {
    if (!dateStr || typeof dateStr !== 'string') return null;
    if (/^\d{4}$/.test(dateStr)) return new Date(`${dateStr}-01-01T00:00:00`);
    if (/^\d{4}-\d{2}$/.test(dateStr)) return new Date(`${dateStr}-01T00:00:00`);
    const date = new Date(`${dateStr}T00:00:00`);
    return isNaN(date.getTime()) ? null : date;
}

function getNodeRadius(node) {
    const degree = node.degree || 1;
    return 5 + Math.sqrt(degree) * 2;
}

// --- New Interval Calculation Functions ---
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

function propagateIntervalChange(originPrefix) {
    const originDate = getDateFromGroup(originPrefix);
    if (!originDate) return;

    if (originPrefix === 'start') {
        const newEndDate = addInterval(originDate, currentInterval);
        updateDateGroup('end', newEndDate);
    } else { // originPrefix === 'end'
        const newStartDate = subtractInterval(originDate, currentInterval);
        updateDateGroup('start', newStartDate);
    }
}


function handleDatePartKeydown(event) {
    const target = event.target;
    if (target.value === '' && (event.key === 'ArrowUp' || event.key === 'ArrowDown')) {
        const now = new Date();
        target.value = target.id.includes('year') ? now.getFullYear() :
            target.id.includes('month') ? String(now.getMonth() + 1).padStart(2, '0') :
            String(now.getDate()).padStart(2, '0');
    }
    const [prefix, part] = target.id.split('-');
    const isAtStart = target.selectionStart === 0 && target.selectionEnd === 0;
    const isAtEnd = target.selectionStart === target.value.length && target.selectionEnd === target.value.length;
    if (event.key === 'ArrowLeft' && isAtStart) {
        const prevInput = getAdjacentInput(prefix, part, 'prev');
        if (prevInput) {
            event.preventDefault();
            prevInput.focus();
            prevInput.setSelectionRange(prevInput.value.length, prevInput.value.length);
        }
    } else if (event.key === 'ArrowRight' && isAtEnd) {
        const nextInput = getAdjacentInput(prefix, part, 'next');
        if (nextInput) {
            event.preventDefault();
            nextInput.focus();
        }
    }
    if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
        event.preventDefault();
        const direction = (event.key === 'ArrowUp') ? 1 : -1;
        const currentDate = getDateFromGroup(prefix) || new Date();
        
        if (isNaN(currentDate.getTime())) return;
        switch (part) {
            case 'year':
                currentDate.setFullYear(currentDate.getFullYear() + direction);
                break;
            case 'month':
                currentDate.setMonth(currentDate.getMonth() + direction);
                break;
            case 'day':
                currentDate.setDate(currentDate.getDate() + direction);
                break;
        }
        updateDateGroup(prefix, currentDate);
        
        // Manually trigger the debounced handler
        dateChangeHandler.call(document.getElementById(`${prefix}-${part}`));
    }
}

const dateChangeHandler = debounce(function() {
    if (isIntervalLocked) {
        const changedPrefix = this.id.split('-')[0];
        propagateIntervalChange(changedPrefix);
    }
    renderGraph();
}, 300);

// ===================================================================
// ===================  核心功能函数 =================================
// ===================================================================

function createLegend() {
    if (!fullGraphData || !Array.isArray(fullGraphData.nodes)) return;
    const nodeTypes = [...new Set(fullGraphData.nodes.map(n => n.type))];
    const customColorMap = {
        'Movement': 'rgb(0, 255, 255)',
        'Person': 'rgb(255, 0, 0)',
        'Organization': 'rgb(25, 40, 200)',
        'Event': 'rgb(170, 30, 170)',
        'Location': 'rgb(225, 170, 30)',
        'Document': 'rgb(15, 255, 50)'
    };
    const defaultColors = d3.schemeCategory10;
    let colorIndex = 0;
    const colorRange = nodeTypes.map(type => {
        if (customColorMap[type]) return customColorMap[type];
        const color = defaultColors[colorIndex % defaultColors.length];
        colorIndex++;
        return color;
    });
    colorScale.domain(nodeTypes).range(colorRange);
    const legendContainer = d3.select("#legend-container");
    legendContainer.selectAll("*").remove();
    nodeTypes.forEach(type => {
        const item = legendContainer.append("div").attr("class", "legend-item");
        const colorBox = item.append("div").attr("class", "color-box").style("background-color", colorScale(type));
        item.append("input").attr("type", "checkbox").attr("checked", true).on("change", function() {
            if (this.checked) {
                hiddenTypes.delete(type);
                colorBox.style("background-color", colorScale(type));
            } else {
                hiddenTypes.add(type);
                colorBox.style("background-color", "#ccc");
            }
            renderGraph();
        });
        item.append("span").text(type);
        item.append("input").attr("type", "color").attr("value", colorScale(type)).on("input", debounce(function() {
            const newColor = this.value;
            const typeIndex = colorScale.domain().indexOf(type);
            const newRange = colorScale.range();
            newRange[typeIndex] = newColor;
            colorScale.range(newRange);
            colorBox.style("background-color", newColor);
            renderGraph();
        }, 100));
    });
}

function renderGraph() {
    if (!fullGraphData) return;

    // 1. Filter data based on date
    const startDate = getDateFromGroup('start');
    const endDateRaw = getDateFromGroup('end');
    const endDate = endDateRaw ? new Date(endDateRaw.getTime() + 24 * 60 * 60 * 1000 - 1) : null;
    
    let visibleNodes = [];
    let validRels = [];

    if (startDate && endDate && endDate < startDate) {
        // Invalid date range, show nothing
    } else {
        const effectiveStartDate = startDate || new Date(-8640000000000000);
        const effectiveEndDate = endDate || new Date(8640000000000000);

        const timeFilteredRels = fullGraphData.relationships.filter(rel => {
            if (!rel || !rel.properties || !rel.properties.start_date) return true;
            const startDates = Array.isArray(rel.properties.start_date) ? rel.properties.start_date : [rel.properties.start_date];
            const endDates = rel.properties.end_date ? (Array.isArray(rel.properties.end_date) ? rel.properties.end_date : [rel.properties.end_date]) : [];
            return startDates.some((startStr, i) => {
                const relStartDate = parseDate(startStr);
                if (!relStartDate) return false;
                const relEndDate = endDates[i] ? parseDate(endDates[i]) : effectiveEndDate;
                return relStartDate <= effectiveEndDate && relEndDate >= effectiveStartDate;
            });
        });

        const activeNodeIds = new Set();
        timeFilteredRels.forEach(rel => {
            if (rel.source) activeNodeIds.add(rel.source);
            if (rel.target) activeNodeIds.add(rel.target);
        });

        let timeFilteredNodes = fullGraphData.nodes.filter(node => node && activeNodeIds.has(node.id));

        // 优化的时间过滤
        timeFilteredNodes = timeFilteredNodes.filter(node => {
            if (node.type === 'Location') return true;
            let nodeDateProp = null;
            if (node.type === 'Person' && node.properties?.lifetime) {
                nodeDateProp = node.properties.lifetime;
            } else if (node.properties?.period) {
                nodeDateProp = node.properties.period;
            }
            if (!nodeDateProp) return true;

            const dateRanges = Array.isArray(nodeDateProp) ? nodeDateProp : [nodeDateProp];

            return dateRanges.some(rangeStr => {
                let startStr, endStr;

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

                const parsedNodeStart = parseDate(startStr);
                const parsedNodeEnd = parseDate(endStr);

                if (startStr && !parsedNodeStart) return true;
                if (endStr && !parsedNodeEnd) return true;

                const finalNodeStart = parsedNodeStart || new Date(-8640000000000000);
                const finalNodeEnd = parsedNodeEnd || new Date(8640000000000000);

                return finalNodeStart <= effectiveEndDate && finalNodeEnd >= effectiveStartDate;
            });
        });


        const degreeCount = {};
        timeFilteredRels.forEach(rel => {
            if (rel.source) degreeCount[rel.source] = (degreeCount[rel.source] || 0) + 1;
            if (rel.target) degreeCount[rel.target] = (degreeCount[rel.target] || 0) + 1;
        });
        timeFilteredNodes.forEach(node => {
            if (node) node.degree = degreeCount[node.id] || 0;
        });

        visibleNodes = timeFilteredNodes.filter(node => node && !hiddenTypes.has(node.type));
        const visibleNodeIds = new Set(visibleNodes.map(n => n.id));
        const visibleRels = timeFilteredRels.filter(rel => rel && visibleNodeIds.has(rel.source) && visibleNodeIds.has(rel.target));
        const nodeById = new Map(visibleNodes.map(node => [node.id, node]));
        validRels = visibleRels.map(link => ({ ...link,
            source: nodeById.get(link.source),
            target: nodeById.get(link.target)
        })).filter(link => link.source && link.target);
    }

    // 2. Set up dynamic anchors
    visibleNodes.forEach(n => n.isAnchor = false);
    const sortedNodes = [...visibleNodes].sort((a, b) => b.degree - a.degree);
    sortedNodes.slice(0, 3).forEach(n => n.isAnchor = true);

    // 3. Group links for rendering
    const linkGroups = {};
    validRels.forEach(link => {
        const pairId = link.source.id < link.target.id ? `${link.source.id}-${link.target.id}` : `${link.target.id}-${link.source.id}`;
        if (!linkGroups[pairId]) linkGroups[pairId] = [];
        linkGroups[pairId].push(link);
    });
    validRels.forEach(link => {
        const pairId = link.source.id < link.target.id ? `${link.source.id}-${link.target.id}` : `${link.target.id}-${link.source.id}`;
        const group = linkGroups[pairId];
        link.groupSize = group.length;
        link.groupIndex = group.indexOf(link);
    });

    neighbors = {};
    validRels.forEach(d => {
        if (d.source && d.target) {
            if (!neighbors[d.source.id]) neighbors[d.source.id] = [];
            if (!neighbors[d.target.id]) neighbors[d.target.id] = [];
            neighbors[d.source.id].push(d.target.id);
            neighbors[d.target.id].push(d.source.id);
        }
    });

    // 4. D3 Data Join
    const linkElements = linkGroup.selectAll("path.link")
        .data(validRels, d => `${d.source.id}-${d.target.id}-${d.type}`)
        .join("path")
        .attr("class", "link")
        .attr("marker-end", d => !nonDirectedTypes.has(d.type) ? "url(#arrowhead)" : null);

    const linkLabelElements = linkLabelGroup.selectAll("text.link-label")
        .data(validRels, d => `${d.source.id}-${d.target.id}-${d.type}`)
        .join("text")
        .attr("class", "link-label")
        .text(d => d.type);

    const nodeElements = nodeGroup.selectAll("g.node")
        .data(visibleNodes, d => d.id)
        .join(
            enter => {
                const g = enter.append("g").attr("class", "node");
                g.append("circle").attr("r", d => getNodeRadius(d)).attr("fill", d => colorScale(d.type)).on("click", highlight);
                g.append("text").attr("dy", ".3em").text(d => d.id);
                return g;
            },
            update => {
                update.select("circle").transition().duration(100).attr("r", d => getNodeRadius(d)).attr("fill", d => colorScale(d.type));
                return update;
            }
        );

    // 5. Setup Interactions
    nodeElements.call(drag(simulation));

    nodeElements.on("mouseover", (event, d) => {
        tooltip.style("opacity", 1)
            .html(`<strong>ID:</strong> ${d.id}<br><strong>Type:</strong> ${d.type}<br><strong>度 (Degree):</strong> ${d.degree || 0}<br><strong>Desc:</strong> ${d.properties ? d.properties.description : 'N/A'}`);
    }).on("mousemove", (event) => {
        tooltip.style("left", (event.pageX + 10) + "px").style("top", (event.pageY + 10) + "px");
    }).on("mouseout", () => {
        tooltip.style("opacity", 0);
    });

    linkElements.on("mouseover", (event, d) => {
        tooltip.style("opacity", 1)
            .html(`<strong>Type:</strong> ${d.type}<br><strong>From:</strong> ${d.source.id}<br><strong>To:</strong> ${d.target.id}<br><strong>Desc:</strong> ${d.properties ? d.properties.description : 'N/A'}`);
    }).on("mousemove", (event) => {
        tooltip.style("left", (event.pageX + 10) + "px").style("top", (event.pageY + 10) + "px");
    }).on("mouseout", () => {
        tooltip.style("opacity", 0);
    });

    // 6. Run Simulation
    simulation.stop();
    simulation.nodes(visibleNodes);
    simulation.force("link").links(validRels);

    simulation.on("tick", () => {
        linkElements.attr("d", d => {
            const source = d.source;
            const target = d.target;
            const targetRadius = getNodeRadius(target);

            if (d.groupSize <= 1) {
                const dx = target.x - source.x;
                const dy = target.y - source.y;
                const dist = Math.sqrt(dx * dx + dy * dy);
                if (dist === 0) return `M${source.x},${source.y}L${target.x},${target.y}`;
                const newTargetX = target.x - (dx / dist) * targetRadius;
                const newTargetY = target.y - (dy / dist) * targetRadius;
                return `M${source.x},${source.y}L${newTargetX},${newTargetY}`;
            } else {
                const dx = target.x - source.x;
                const dy = target.y - source.y;
                const side = (d.groupIndex % 2 === 0) ? 1 : -1;
                const rank = Math.ceil(d.groupIndex / 2);
                const curvature = rank * 0.15 * side;
                const midX = (source.x + target.x) / 2;
                const midY = (source.y + target.y) / 2;
                const cx = midX - curvature * dy;
                const cy = midY + curvature * dx;
                const cdx = target.x - cx;
                const cdy = target.y - cy;
                const cDist = Math.sqrt(cdx * cdx + cdy * cdy);
                if (cDist === 0) return `M${source.x},${source.y}Q${cx},${cy} ${target.x},${target.y}`;
                const newTargetX = target.x - (cdx / cDist) * targetRadius;
                const newTargetY = target.y - (cdy / cDist) * targetRadius;
                return `M${source.x},${source.y}Q${cx},${cy} ${newTargetX},${newTargetY}`;
            }
        });

        linkLabelElements.attr("transform", function(d) {
            if (!d.source || !d.target) return "";
            let midX, midY;
            if (d.groupSize > 1) {
                const dx = d.target.x - d.source.x;
                const dy = d.target.y - d.source.y;
                const midPointX = (d.source.x + d.target.x) / 2;
                const midPointY = (d.source.y + d.target.y) / 2;
                const side = (d.groupIndex % 2 === 0) ? 1 : -1;
                const rank = Math.ceil(d.groupIndex / 2);
                const curvature = rank * 0.15 * side;
                const cx = midPointX - curvature * dy;
                const cy = midPointY + curvature * dx;
                midX = 0.25 * d.source.x + 0.5 * cx + 0.25 * d.target.x;
                midY = 0.25 * d.source.y + 0.5 * cy + 0.25 * d.target.y;
            } else {
                midX = (d.source.x + d.target.x) / 2;
                midY = (d.source.y + d.target.y) / 2;
            }
            return `translate(${midX}, ${midY})`;
        });

        nodeElements.attr("transform", d => `translate(${d.x},${d.y})`);
    });

    simulation.alpha(0.3).restart();
}

function drag(simulation) {
    function dragstarted(event, d) {
        if (!event.active) simulation.alphaTarget(0.3).restart();
        d.fx = d.x;
        d.fy = d.y;
    }

    function dragged(event, d) {
        d.fx = event.x;
        d.fy = event.y;
        tooltip.style("left", (event.sourceEvent.pageX + 10) + "px").style("top", (event.sourceEvent.pageY + 10) + "px");
    }

    function dragended(event, d) {
        if (!event.active) simulation.alphaTarget(0);
        if (!d.isAnchor) { // Only unfix non-anchor nodes
            d.fx = null;
            d.fy = null;
        }
    }
    return d3.drag().on("start", dragstarted).on("drag", dragged).on("end", dragended);
}

let selectedNode = null;

function highlight(event, d) {
    const isHighlighting = selectedNode !== d.id;
    selectedNode = isHighlighting ? d.id : null;

    // 获取当前高亮操作的目标颜色，如果取消高亮则为null
    const highlightColor = isHighlighting ? colorScale(d.type) : null;

    // 定义判断条件
    const nodeIsFaded = n => n.id !== d.id && !neighbors[d.id]?.includes(n.id);
    const linkIsRelated = l => l.source.id === d.id || l.target.id === d.id;

    // 处理节点
    nodeGroup.selectAll(".node")
        .classed("faded", isHighlighting && nodeIsFaded)
        .classed("highlight", isHighlighting && (n => n.id === d.id));

    // --- 直接用JS动态设置高亮连线的颜色 ---
    linkGroup.selectAll(".link")
        .classed("faded", isHighlighting && (l => !linkIsRelated(l)))
        .style("stroke", l => {
            if (isHighlighting && linkIsRelated(l)) {
                return highlightColor; // 应用动态高亮色
            }
            return "#fff"; // 恢复默认颜色
        })
        .style("stroke-opacity", l => {
            if (isHighlighting && linkIsRelated(l)) {
                return 1.0; // 高亮时不透明
            }
            return 0.6; // 恢复默认透明度
        });

    // 处理关系文字
    linkLabelGroup.selectAll(".link-label")
        .classed("faded", isHighlighting && (l => !linkIsRelated(l)));

    // 用JS直接改变全局箭头的颜色
    svg.select("#arrowhead path")
        .style('fill', isHighlighting ? highlightColor : '#999'); // 高亮时为动态色，否则为默认灰色

    event.stopPropagation();
}

async function initialize() {
    svg.append('defs').append('marker')
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
        .style('fill', '#999');

    const zoom = d3.zoom().on("zoom", (event) => {
        container.attr("transform", event.transform);
    });
    svg.call(zoom);

    svg.on("click", () => {
        selectedNode = null;
        nodeGroup.selectAll(".node").classed("faded", false).classed("highlight", false);
        linkGroup.selectAll(".link")
            .classed("faded", false)
            .style("stroke", "#fff")
            .style("stroke-opacity", 0.6);
        linkLabelGroup.selectAll(".link-label").classed("faded", false);
        svg.select("#arrowhead path").style('fill', '#999');
    });

    // --- New Interval Controls Event Listeners ---
    const toggleBtn = document.getElementById('toggle-interval-btn');
    const intervalInputsWrapper = document.getElementById('interval-inputs-wrapper');
    const setBtn = document.getElementById('set-interval-btn');
    const clearBtn = document.getElementById('clear-interval-btn');

    toggleBtn.addEventListener('click', (e) => {
        e.preventDefault();
        intervalInputsWrapper.classList.toggle('hidden');
    });

    setBtn.addEventListener('click', () => {
        const years = parseInt(document.getElementById('interval-year').value) || 0;
        const months = parseInt(document.getElementById('interval-month').value) || 0;
        const days = parseInt(document.getElementById('interval-day').value) || 0;
        
        currentInterval = { years, months, days };
        isIntervalLocked = true;
        
        propagateIntervalChange('start'); // Align to start date by default
        renderGraph();
        
        clearBtn.classList.remove('hidden');
        intervalInputsWrapper.classList.add('hidden');
    });

    clearBtn.addEventListener('click', () => {
        isIntervalLocked = false;
        currentInterval = { years: 0, months: 0, days: 0 };
        clearBtn.classList.add('hidden');
    });


    // --- Date Input Event Listeners ---
    document.querySelectorAll('.date-part').forEach(input => {
        input.addEventListener('keydown', handleDatePartKeydown);
        input.addEventListener('input', (e) => {
            e.target.value = e.target.value.replace(/[^0-9]/g, '');
            dateChangeHandler.call(e.target);
        });
        input.addEventListener('change', dateChangeHandler);
    });

    try {
        const response = await fetch(DATA_FILE_URL);
        if (!response.ok) {
            throw new Error(`Network response was not ok. Status: ${response.status}`);
        }
        fullGraphData = await response.json();
        if (fullGraphData) {
            createLegend();
            renderGraph();
        } else {
            throw new Error("Loaded data is null or empty.");
        }
    } catch (error) {
        console.error('Failed to fetch and initialize graph:', error);
        d3.select("#graph-container").html(`<h2>错误：无法加载关系图谱数据。</h2><p>详细错误: <code>${error.message}</code></p>`);
    }
}
