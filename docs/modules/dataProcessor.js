// docs/modules/dataProcessor.js

import CONFIG from './config.js';
import { parseDate, expandVagueDate } from './utils.js';

/**
 * DataProcessor 类负责所有数据的加载、处理和分析。
 * 它持有原始的完整图谱数据，并根据当前状态提供过滤后的可见数据。
 */
export class DataProcessor {
    constructor(onDataChange) {
        this.onDataChange = onDataChange;

        this.currentGraphData = { nodes: [], relationships: [] };
        this.nodeMap = new Map();
        
        this.simpleDatabaseCache = new Map();
        this.initialNodeIds = new Set();
        this.nameToIdMap = {};

        this.destructionSchedule = new Map();
        this.startDestructionScheduler();
    }

    // 启动全局唯一的销毁调度器
    startDestructionScheduler() {
        setInterval(() => {
            const now = Date.now();
            let changed = false;
            this.destructionSchedule.forEach((scheduledTime, nodeId) => {
                if (scheduledTime !== null && now > scheduledTime) {
                    this._removeNode(nodeId);
                    this.destructionSchedule.delete(nodeId); // 从计划表中移除
                    changed = true;
                }
            });
            if (changed) {
                this.onDataChange(); // 如果有节点被销毁，则触发一次UI更新
            }
        }, 15000); // 每15秒检查一次
    }

    // 为节点安排销毁计划
    scheduleNodeDestruction(nodeId) {
        // 只有当节点当前没有销毁计划时，才为它安排一个新的
        if (!this.destructionSchedule.has(nodeId) || this.destructionSchedule.get(nodeId) === null) {
            this.destructionSchedule.set(nodeId, Date.now() + CONFIG.TEMPORARY_NODE_TTL);
        }
    }

    // 取消节点的销毁计划
    cancelNodeDestruction(nodeId) {
        // 如果节点之前有销毁计划，则将其取消（设为null）
        if (this.destructionSchedule.has(nodeId)) {
            this.destructionSchedule.set(nodeId, null);
        }
    }

    async loadData() {
        const response = await fetch(CONFIG.DATA_FILE_URL);
        if (!response.ok) throw new Error(`Network response was not ok. Status: ${response.status}`);
        const data = await response.json();
        if (!data) throw new Error("Loaded data is null or empty.");

        this.currentGraphData = data;
        this.currentGraphData.nodes.forEach(node => {
            if (node) {
                this.nodeMap.set(node.id, node);
                this.initialNodeIds.add(node.id);
            }
        });

        try {
            const nameMapResponse = await fetch(CONFIG.NAME_TO_ID_URL);
            if (nameMapResponse.ok) {
                this.nameToIdMap = await nameMapResponse.json();
            }
        } catch (error) {
            console.error('Failed to load name-to-id map:', error);
        }

        return true;
    }

    // 从简单数据库搜索节点
    async _fetchNodeFromSimpleDB(nodeId) {
        if (this.simpleDatabaseCache.has(nodeId)) {
            return this.simpleDatabaseCache.get(nodeId);
        }
        try {
            const safeNodeId = nodeId.replace(":", "_");
            const response = await fetch(`${CONFIG.DATA_DIR}nodes/${safeNodeId}/node.json`);
            if (!response.ok) {
                this.simpleDatabaseCache.set(nodeId, null);
                return null;
            }
            const data = await response.json();
            this.simpleDatabaseCache.set(nodeId, data);
            return data;
        } catch (error) {
            console.error(`Failed to fetch node ${nodeId} from simple DB:`, error);
            this.simpleDatabaseCache.set(nodeId, null);
            return null;
        }
    }
    
    // 加载邻居节点
    async streamAndAddNeighbors(nodeId) {
        const sourceNodeData = await this._fetchNodeFromSimpleDB(nodeId);
        // 如果初始节点数据或关系加载失败，返回空数据
        if (!sourceNodeData || !sourceNodeData.relationships) return { nodes: [], links: [] };

        const neighborIds = new Set();
        sourceNodeData.relationships.forEach(rel => {
            const neighborId = rel.source === nodeId ? rel.target : rel.source;
            neighborIds.add(neighborId);
        });

        const newNodes = [];
        const newLinks = [];

        for (const neighborId of neighborIds) {
            // 只处理当前图中确实不存在的邻居
            if (!this.nodeMap.has(neighborId)) {
                const neighborData = await this._fetchNodeFromSimpleDB(neighborId);
                if (neighborData && neighborData.node) {
                    newNodes.push(neighborData.node);
                }
            }
        }

        // 将所有相关的链接都收集起来
        sourceNodeData.relationships.forEach(rel => {
            newLinks.push(rel);
        });
        
        // 返回一个包含新节点和新链接的对象
        return { nodes: newNodes, links: newLinks };
    }

    _addNodesAndLinksToGraph(nodesToAdd, linksToAdd) {
        let changed = false;
        nodesToAdd.forEach(node => {
            if (node && !this.nodeMap.has(node.id)) {
                this.nodeMap.set(node.id, node);
                this.currentGraphData.nodes.push(node);
                changed = true;
            }
        });

        linksToAdd.forEach(link => {
            // 从 link 对象中提取源和目标ID（此时它们是Q-Code字符串）
            const newSourceId = link.source;
            const newTargetId = link.target;
            
            const linkExists = this.currentGraphData.relationships.some(existingRel => {
                // D3力导向图会把ID替换为节点对象，因此需兼容处理以正确获取ID
                const existingSourceId = existingRel.source.id || existingRel.source;
                const existingTargetId = existingRel.target.id || existingRel.target;

                if (existingRel.type !== link.type) {
                    return false;
                }

                const isSameDirection = existingSourceId === newSourceId && existingTargetId === newTargetId;
                const isReverseDirection = existingSourceId === newTargetId && existingTargetId === newSourceId;

                // 如果是无向关系（如配偶、朋友），则正向和反向都视为重复
                if (CONFIG.NON_DIRECTED_LINK_TYPES.has(link.type)) {
                    return isSameDirection || isReverseDirection;
                }
                else {
                    // 如果是有向关系，则必须是完全相同的方向才算重复
                    return isSameDirection;
                }
            });

            if (!linkExists) {
                this.currentGraphData.relationships.push(link);
                changed = true;
            }
        });
        
        return changed;
    }

    _removeNode(nodeId) {
        this.currentGraphData.nodes = this.currentGraphData.nodes.filter(n => n.id !== nodeId);
        this.currentGraphData.relationships = this.currentGraphData.relationships.filter(
            r => {
                if (!r || !r.source || !r.target) return false;
                const sourceId = r.source.id || r.source;
                const targetId = r.target.id || r.target;
                return sourceId !== nodeId && targetId !== nodeId;
            }
        );
        this.nodeMap.delete(nodeId);
    }

    getVisibleData(state) {
        const { startDate, endDate, hiddenTypes, pinnedNodeIds } = state;
        const effectiveStartDate = startDate || new Date(-8640000000000000);
        const effectiveEndDate = endDate ? new Date(endDate.getTime() + 24 * 60 * 60 * 1000 - 1) : new Date(8640000000000000);
        if (startDate && endDate && endDate < startDate) {
            return { visibleNodes: [], validRels: [], neighbors: {} };
        }
        const timeFilteredRels = this.currentGraphData.relationships.filter(rel => this._isRelActive(rel, effectiveStartDate, effectiveEndDate));

        const activeNodeIds = new Set();
        timeFilteredRels.forEach(rel => {
            if (rel && rel.source && rel.target) {
                const sourceId = rel.source.id || rel.source;
                const targetId = rel.target.id || rel.target;
                if (sourceId) activeNodeIds.add(sourceId);
                if (targetId) activeNodeIds.add(targetId);
            }
        });

        // 确保被钉住的节点总是可见的，即使它们没有在当前时间范围内的连接
        pinnedNodeIds.forEach((reason, nodeId) => activeNodeIds.add(nodeId));

        const connectedNodes = this.currentGraphData.nodes.filter(node => node && activeNodeIds.has(node.id));
        const timeFilteredNodes = connectedNodes.filter(node => this._isNodeActive(node, effectiveStartDate, effectiveEndDate));
        const degreeCount = {};
        timeFilteredRels.forEach(rel => {
            if (rel && rel.source && rel.target) {
                const sourceId = rel.source.id || rel.source;
                const targetId = rel.target.id || rel.target;
                if (sourceId) degreeCount[sourceId] = (degreeCount[sourceId] || 0) + 1;
                if (targetId) degreeCount[targetId] = (degreeCount[targetId] || 0) + 1;
            }
        });
        timeFilteredNodes.forEach(node => {
            if (node) node.degree = degreeCount[node.id] || 0;
        });
        const visibleNodes = timeFilteredNodes.filter(node => node && !hiddenTypes.has(node.type));
        const visibleNodeIds = new Set(visibleNodes.map(n => n.id));

        const visibleRels = timeFilteredRels.filter(rel => {
            if (!rel || !rel.source || !rel.target) {
                return false;
            }
            const sourceId = rel.source.id || rel.source;
            const targetId = rel.target.id || rel.target;
            
            return sourceId && targetId && visibleNodeIds.has(sourceId) && visibleNodeIds.has(targetId);
        });

        const nodeById = new Map(visibleNodes.map(node => [node.id, node]));
        const validRels = visibleRels
            .map(link => ({ ...link, source: nodeById.get(link.source.id || link.source), target: nodeById.get(link.target.id || link.target) }))
            .filter(link => link.source && link.target); 
        const neighbors = this._buildNeighborMap(validRels);
        return { visibleNodes, validRels, neighbors };
    }

    findNode(name, visibleNodes) {
        // 查找'zh-cn'下的所有项，以支持别名搜索
        const isMatch = n => {
            if (n.id === name) return true;
            if (n.name && typeof n.name === 'object') {
                for (const lang in n.name) {
                    if (Array.isArray(n.name[lang]) && n.name[lang].includes(name)) {
                        return true;
                    }
                }
            }
            return false;
        };

        let node = visibleNodes.find(isMatch);
        if (node) return { node, isVisible: true };

        node = this.currentGraphData.nodes.find(isMatch);
        return { node: node || null, isVisible: false };
    }

    async findAndLoadNodeData(name) {
        const isMatch = n => {
            if (n.id === name) return true;
            if (n.name && typeof n.name === 'object') {
                for (const lang in n.name) {
                    if (Array.isArray(n.name[lang]) && n.name[lang].includes(name)) {
                        return true;
                    }
                }
            }
            return false;
        };

        // 1. 优先在已加载的数据中进行搜索
        let node = this.currentGraphData.nodes.find(isMatch);
        if (node) {
            return node;
        }

        // 2. 若未找到, 使用全局名称映射表查找ID
        const nodeId = this.nameToIdMap[name];
        if (!nodeId) {
            return null;
        }

        // 3. 再次确认该ID是否已在图中
        if (this.nodeMap.has(nodeId)) {
            return this.nodeMap.get(nodeId);
        }

        // 4. 从服务器获取节点数据并返回
        const nodeData = await this._fetchNodeFromSimpleDB(nodeId);
        return nodeData?.node || null;
    }

    isNodeCompatibleWithFilters(nodeData, state) {
        const { startDate, endDate, hiddenTypes } = state;
        if (!nodeData) return false;

        // 1. 节点的类型是否被图例隐藏
        if (hiddenTypes.has(nodeData.type)) {
            return false;
        }

        // 2. 节点自身的活跃时间是否在当前设定的时间范围内
        const effectiveStartDate = startDate || new Date(-8640000000000000);
        const effectiveEndDate = endDate ? new Date(endDate.getTime() + 24 * 60 * 60 * 1000 - 1) : new Date(8640000000000000);
        
        if (startDate && endDate && endDate < startDate) {
            return false;
        }

        if (!this._isNodeActive(nodeData, effectiveStartDate, effectiveEndDate)) {
            return false;
        }

        // 如果所有检查都通过，则该节点与当前过滤器兼容
        return true;
    }

    findPaths(startNodeId, endNodeId, limit, validRels) { // 接收 validRels 作为参数
        const queue = [[startNodeId]];
        const foundPaths = [];

        // 设置一个安全的迭代上限，以防止在大型不连通图中搜索时浏览器卡死
        const MAX_SEARCH_ITERATIONS = 10000;
        let iterations = 0;

        const adjacencyList = new Map();
        // 在过滤后的关系列表上构建邻接表
        validRels.forEach(rel => {
            const sourceId = rel.source.id || rel.source;
            const targetId = rel.target.id || rel.target;

            if (!adjacencyList.has(sourceId)) adjacencyList.set(sourceId, []);
            if (!adjacencyList.has(targetId)) adjacencyList.set(targetId, []);

            adjacencyList.get(sourceId).push(targetId);
            adjacencyList.get(targetId).push(sourceId);
        });

        while (queue.length > 0) {
            iterations++;
            // 熔断机制：如果搜索过于广泛，则中止以防止冻结
            if (iterations > MAX_SEARCH_ITERATIONS) {
                console.warn(`Path search aborted after ${MAX_SEARCH_ITERATIONS} iterations to prevent freezing.`);
                break;
            }

            if (foundPaths.length >= limit) break;
            
            const currentPath = queue.shift();
            const lastNodeId = currentPath[currentPath.length - 1];

            if (lastNodeId === endNodeId) {
                foundPaths.push(currentPath);
                continue;
            }
            
            // 限制路径深度，这是一个辅助性的性能优化
            if (currentPath.length > 10) continue;

            const neighbors = adjacencyList.get(lastNodeId) || [];
            for (const neighborId of neighbors) {
                if (!currentPath.includes(neighborId)) {
                    const newPath = [...currentPath, neighborId];
                    queue.push(newPath);
                }
            }
        }

        return { paths: foundPaths, nodesToAdd: [], linksToAdd: [] };
    }
    
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
        }
        else if (node.properties?.period) {
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
            }
            else if (cleanedRangeStr.includes('—')) {
                parts = cleanedRangeStr.split('—');
            }
            else if (cleanedRangeStr.includes('–')) {
                parts = cleanedRangeStr.split('–');
            }
            else if (/^\d{4}\s*-\s*\d{4}$/.test(cleanedRangeStr)) {
                parts = cleanedRangeStr.split('-');
            }

            if (parts && parts.length >= 2) {
                startStr = parts[0].trim();
                endStr = parts.slice(1).join('').trim();
            }
            else if (cleanedRangeStr.endsWith('-')) {
                startStr = cleanedRangeStr.slice(0, -1).trim();
                endStr = '';
            }
            else if (cleanedRangeStr.startsWith('-')) {
                startStr = '';
                endStr = cleanedRangeStr.slice(1).trim();
            }
            else {
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
            if (d.source && d.target) {
                if (!adjacencyList.has(rel.source.id)) adjacencyList.set(rel.source.id, []);
                if (!adjacencyList.has(rel.target.id)) adjacencyList.set(rel.target.id, []);
                adjacencyList.get(rel.source.id).push({ id: rel.target.id, weight: 1 });
                adjacencyList.get(rel.target.id).push({ id: rel.source.id, weight: 1 });
            }
        });
        return adjacencyList;
    }
}
