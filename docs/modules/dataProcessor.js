// docs/modules/dataProcessor.js
import CONFIG from './config.js';
import { parseDate, expandVagueDate } from './utils.js';

/**
 * DataProcessor 类负责所有数据的加载、处理和分析。
 * 它持有原始的完整图谱数据，并根据当前状态提供过滤后的可见数据。
 */
export class DataProcessor {
    constructor() {
        // 存放从JSON加载的完整、未经修改的图谱数据
        this.fullGraphData = { nodes: [], relationships: [] };
        // 建立一个从节点ID到节点对象的映射，用于快速查找
        this.nodeMap = new Map();
    }

    /**
     * 异步加载图谱数据文件。
     * @returns {Promise<boolean>} 数据加载成功时返回true
     */
    async loadData() {
        const response = await fetch(CONFIG.DATA_FILE_URL);
        if (!response.ok) throw new Error(`Network response was not ok. Status: ${response.status}`);
        const data = await response.json();
        if (!data) throw new Error("Loaded data is null or empty.");
        
        this.fullGraphData = data;
        // 填充nodeMap以便快速访问
        this.fullGraphData.nodes.forEach(node => {
            if (node) this.nodeMap.set(node.id, node);
        });
        return true;
    }

    /**
     * 根据当前状态（时间、类型等）过滤数据，返回可供渲染的图谱。
     * @param {object} state - 当前应用的状态对象
     * @returns {{visibleNodes: Array, validRels: Array, neighbors: object}}
     */
    getVisibleData(state) {
        const { startDate, endDate, hiddenTypes } = state;

        // 1. 确定有效的时间范围
        const effectiveStartDate = startDate || new Date(-8640000000000000);
        const effectiveEndDate = endDate ? new Date(endDate.getTime() + 24 * 60 * 60 * 1000 - 1) : new Date(8640000000000000);
        
        if (startDate && endDate && endDate < startDate) {
            return { visibleNodes: [], validRels: [], neighbors: {} };
        }

        // 2. 筛选在时间范围内的关系
        const timeFilteredRels = this.fullGraphData.relationships.filter(rel => this._isRelActive(rel, effectiveStartDate, effectiveEndDate));
        
        // 3. 找出所有活跃关系涉及的节点ID
        const activeNodeIds = new Set();
        timeFilteredRels.forEach(rel => {
            if (rel.source) activeNodeIds.add(rel.source);
            if (rel.target) activeNodeIds.add(rel.target);
        });

        // 4. 从完整节点列表中筛选出这些节点，并再次按节点自身的时间范围过滤
        const connectedNodes = this.fullGraphData.nodes.filter(node => node && activeNodeIds.has(node.id));
        const timeFilteredNodes = connectedNodes.filter(node => this._isNodeActive(node, effectiveStartDate, effectiveEndDate));
        
        // 5. 计算在当前可见范围内的每个节点的度（连接数）
        const degreeCount = {};
        timeFilteredRels.forEach(rel => {
            if (rel.source) degreeCount[rel.source] = (degreeCount[rel.source] || 0) + 1;
            if (rel.target) degreeCount[rel.target] = (degreeCount[rel.target] || 0) + 1;
        });
        timeFilteredNodes.forEach(node => {
            if (node) node.degree = degreeCount[node.id] || 0;
        });

        // 6. 根据图例中隐藏的类型，进一步筛选节点
        const visibleNodes = timeFilteredNodes.filter(node => node && !hiddenTypes.has(node.type));
        const visibleNodeIds = new Set(visibleNodes.map(n => n.id));

        // 7. 最后，筛选出那些源节点和目标节点都可见的关系
        const visibleRels = timeFilteredRels.filter(rel => rel && visibleNodeIds.has(rel.source) && visibleNodeIds.has(rel.target));

        // 8. 将关系中的 source/target 从ID字符串替换为完整的节点对象，这是D3力导向图所需要的格式
        const nodeById = new Map(visibleNodes.map(node => [node.id, node]));
        const validRels = visibleRels
            .map(link => ({ ...link, source: nodeById.get(link.source), target: nodeById.get(link.target) }))
            .filter(link => link.source && link.target);

        // 9. 构建邻接信息，用于高亮显示
        const neighbors = this._buildNeighborMap(validRels);

        return { visibleNodes, validRels, neighbors };
    }

    /**
     * 根据ID或别名查找节点。
     * @param {string} name - 要搜索的名称
     * @param {Array} visibleNodes - 当前可见的节点数组
     * @returns {{node: object|null, isVisible: boolean}}
     */
    findNode(name, visibleNodes) {
        // 优先在可见节点中查找，效率更高
        let node = visibleNodes.find(n => n.id === name || (n.aliases && n.aliases.includes(name)));
        if (node) return { node, isVisible: true };

        // 如果在可见节点中找不到，则在完整数据中查找，以便提供更准确的错误提示
        node = this.fullGraphData.nodes.find(n => n.id === name || (n.aliases && n.aliases.includes(name)));
        return { node: node || null, isVisible: false };
    }

    /**
     * 使用广度优先搜索（BFS）在当前可见的图中查找两个节点间的最短路径。
     * @param {string} startNodeId - 起始节点ID
     * @param {string} endNodeId - 目标节点ID
     * @param {number} limit - 最多查找的路径数量
     * @param {Array} validRels - 当前可见的关系数组
     * @returns {Array<Array<string>>} 找到的路径数组，每个路径是节点ID的数组
     */
    findPaths(startNodeId, endNodeId, limit, validRels) {
        const adjacencyList = this._buildAdjacencyList(validRels);
        const queue = [[startNodeId]]; // 队列中存放的是路径
        const foundPaths = [];
        
        while (queue.length > 0) {
            if (foundPaths.length >= limit) break;
            const currentPath = queue.shift();
            const lastNode = currentPath[currentPath.length - 1];

            if (lastNode === endNodeId) {
                foundPaths.push(currentPath);
                continue;
            }
            if (currentPath.length > 10) continue; // 限制最大搜索深度，防止性能问题

            const neighbors = adjacencyList.get(lastNode) || [];
            for (const neighbor of neighbors) {
                if (!currentPath.includes(neighbor.id)) { // 防止路径中出现环
                    const newPath = [...currentPath, neighbor.id];
                    queue.push(newPath);
                }
            }
        }
        return foundPaths.sort((a, b) => a.length - b.length);
    }
    
    // --- "私有" 辅助方法 ---

    _isRelActive(rel, start, end) {
        if (!rel?.properties?.start_date) return true;
        const startDates = Array.isArray(rel.properties.start_date) ? rel.properties.start_date : [rel.properties.start_date];
        const endDates = rel.properties.end_date ? (Array.isArray(rel.properties.end_date) ? rel.properties.end_date : [rel.properties.end_date]) : [];
        
        return startDates.some((startStr, i) => {
            const relStart = parseDate(startStr);
            if (!relStart) return false;
            
            const relEndStr = endDates[i];
            let relEnd = relEndStr ? parseDate(relEndStr) : end;
            relEnd = expandVagueDate(relEndStr, relEnd);
            
            return relStart <= end && relEnd >= start;
        });
    }
    
    _isNodeActive(node, start, end) {
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
            let startStr = '', endStr = '';
            const cleanedRangeStr = rangeStr.trim();
            let parts;
            if (cleanedRangeStr.includes(' - ')) {
                parts = cleanedRangeStr.split(' - ');
            } else if (cleanedRangeStr.includes('—')) {
                parts = cleanedRangeStr.split('—');
            } else if (cleanedRangeStr.includes('–')) {
                parts = cleanedRangeStr.split('–');
            } else if (/^\d{4}\s*-\s*\d{4}$/.test(cleanedRangeStr)) {
                parts = cleanedRangeStr.split('-');
            }

            if (parts && parts.length >= 2) {
                startStr = parts[0].trim();
                endStr = parts.slice(1).join('').trim();
            } else if (cleanedRangeStr.endsWith('-')) {
                startStr = cleanedRangeStr.slice(0, -1).trim();
                endStr = '';
            } else if (cleanedRangeStr.startsWith('-')) {
                startStr = '';
                endStr = cleanedRangeStr.slice(1).trim();
            } else {
                startStr = cleanedRangeStr;
                endStr = cleanedRangeStr;
            }
            
            const parsedNodeStart = parseDate(startStr);
            let parsedNodeEnd = parseDate(endStr);
            if (startStr === endStr) {
                parsedNodeEnd = expandVagueDate(startStr, parsedNodeEnd);
            }
            if ((startStr && !parsedNodeStart) || (endStr && !parsedNodeEnd)) {
                return false;
            }
            
            const finalNodeStart = parsedNodeStart || new Date(-8640000000000000);
            const finalNodeEnd = parsedNodeEnd || new Date(8640000000000000);
            
            return finalNodeStart <= end && finalNodeEnd >= start;
        });
    }

    _buildNeighborMap(validRels) {
        const neighbors = {};
        validRels.forEach(d => {
            if (d.source && d.target) {
                if (!neighbors[d.source.id]) neighbors[d.source.id] = [];
                if (!neighbors[d.target.id]) neighbors[d.target.id] = [];
                neighbors[d.source.id].push(d.target.id);
                neighbors[d.target.id].push(d.source.id);
            }
        });
        return neighbors;
    }
    
    _buildAdjacencyList(validRels) {
        const adjacencyList = new Map();
        validRels.forEach(rel => {
            if (rel.source && rel.target) {
                if (!adjacencyList.has(rel.source.id)) adjacencyList.set(rel.source.id, []);
                if (!adjacencyList.has(rel.target.id)) adjacencyList.set(rel.target.id, []);
                adjacencyList.get(rel.source.id).push({ id: rel.target.id, weight: 1 });
                adjacencyList.get(rel.target.id).push({ id: rel.source.id, weight: 1 });
            }
        });
        return adjacencyList;
    }
}
