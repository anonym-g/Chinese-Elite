// docs/main.js
import { stateManager } from './modules/state.js';
import { DataProcessor } from './modules/dataProcessor.js';
import { GraphView } from './modules/graphView.js';
import { UIController } from './modules/uiController.js';

const nodeFixTimers = new Map();

document.addEventListener('DOMContentLoaded', async () => {
    
    stateManager.initialize();

    // 集中处理销毁计划的更新逻辑
    const updateDestructionSchedules = (currentState, neighbors) => {
        const activeNodeIds = new Set(currentState.pinnedNodeIds.keys());
        if (currentState.selectedNodeId) {
            activeNodeIds.add(currentState.selectedNodeId);
            (neighbors[currentState.selectedNodeId] || []).forEach(id => activeNodeIds.add(id));
        }

        // 遍历所有当前存在的节点
        dataProcessor.currentGraphData.nodes.forEach(node => {
            // 不处理 initial 节点
            if (dataProcessor.initialNodeIds.has(node.id)) return;

            // 如果节点是活跃的，取消其销毁计划
            if (activeNodeIds.has(node.id)) {
                dataProcessor.cancelNodeDestruction(node.id);
            }
            else {
                // 如果节点不活跃，安排其销毁计划
                dataProcessor.scheduleNodeDestruction(node.id);
            }
        });
    };

    const mainUpdate = (currentState) => {
        const { visibleNodes, validRels, neighbors } = dataProcessor.getVisibleData(currentState);
        graphView.render({ visibleNodes, validRels });
        graphView.updateHighlights(currentState.selectedNodeId, neighbors);

        updateDestructionSchedules(currentState, neighbors);
    };

    // 在首次渲染后，延时一小段时间（等待D3开始布局），然后设置初始视图
    setTimeout(() => {
        graphView.setInitialView();
    }, 100);

    const dataProcessor = new DataProcessor(() => mainUpdate(stateManager.getState()));

    const graphView = new GraphView('#graph', {
        onNodeClick: async (event, d) => {
            event.stopPropagation();
            const currentState = stateManager.getState();
            const isDeselecting = currentState.selectedNodeId === d.id;

            // 在处理当前点击之前，先清理上一个被选中节点的临时固定状态
            if (currentState.selectedNodeId && currentState.selectedNodeId !== d.id) {
                const previousNode = dataProcessor.nodeMap.get(currentState.selectedNodeId);
                if (previousNode) {
                    clearTimeout(nodeFixTimers.get(previousNode.id));
                    nodeFixTimers.delete(previousNode.id);
                    previousNode.fx = null;
                    previousNode.fy = null;
                }
            }

            stateManager.pinNodes([d.id], 'click');
            
            if (isDeselecting) {
                // 如果是取消选中，也清理当前节点的定时器和固定状态
                clearTimeout(nodeFixTimers.get(d.id));
                nodeFixTimers.delete(d.id);
                d.fx = null;
                d.fy = null;
                stateManager.clearSelection();
                return; 
            }

            // 选中新节点：固定其位置，并设置一个1.5秒后自动解除的定时器
            d.fx = d.x;
            d.fy = d.y;
            const timer = setTimeout(() => {
                if (dataProcessor.nodeMap.has(d.id)) {
                    d.fx = null;
                    d.fy = null;
                }
                nodeFixTimers.delete(d.id);
            }, 1500);
            nodeFixTimers.set(d.id, timer);
            
            stateManager.setSelectedNode(d.id);
            graphView.setAnimationSource(d);
            const graphChanged = await dataProcessor.streamAndAddNeighbors(d.id);

            if (graphChanged) {
                mainUpdate(stateManager.getState()); 
                graphView.updateLegend(dataProcessor.currentGraphData.nodes);
            }
        },
        onSvgClick: () => {
            const currentState = stateManager.getState();
            if (currentState.selectedNodeId) {
                const selectedNode = dataProcessor.nodeMap.get(currentState.selectedNodeId);
                if (selectedNode) {
                    // 点击背景取消选中时，立即清理定时器并解除固定
                    clearTimeout(nodeFixTimers.get(selectedNode.id));
                    nodeFixTimers.delete(selectedNode.id);
                    selectedNode.fx = null;
                    selectedNode.fy = null;
                }
            }
            
            if (currentState.isPathHighlighting) {
                stateManager.unpinNodesByReason('path');
            }
            stateManager.clearSelection();
        },
        onLegendToggle: (event, type) => {
            stateManager.toggleHiddenType(type, !event.target.checked);
        },
        onColorChange: () => {
            mainUpdate(stateManager.getState());
        }
    });

    const uiController = new UIController({
        onNodeSearch: async (query) => {
            // 1. 查找节点并检查其是否与当前过滤器兼容
            const nodeData = await dataProcessor.findAndLoadNodeData(query);
            if (!nodeData) {
                uiController.showErrorToast(`节点 "${query}" 不存在。`);
                return;
            }

            const isCompatible = dataProcessor.isNodeCompatibleWithFilters(nodeData, stateManager.getState());
            if (!isCompatible) {
                const displayName = nodeData.name?.['zh-cn']?.[0] || nodeData.id;
                uiController.showErrorToast(`节点 "${displayName}" (${nodeData.id}) 存在，但当前未被显示。请检查图例或时间范围。`);
                return;
            }

            const isNewNode = !dataProcessor.nodeMap.has(nodeData.id);
            uiController._closeSearchPanel();

            if (isNewNode) {
                // --- 节点动画加载 ---
                graphView.dimGraph();

                // 在当前视窗中心初始化新节点位置，并暂时固定它
                const initialPosition = graphView.getCenterOfView();
                nodeData.x = initialPosition.x;
                nodeData.y = initialPosition.y;
                nodeData.fx = initialPosition.x; 
                nodeData.fy = initialPosition.y;

                // 将节点添加到数据中并钉住，然后更新视图以显示这个孤立的节点
                dataProcessor._addNodesAndLinksToGraph([nodeData], []);
                stateManager.pinNodes([nodeData.id], 'click');
                mainUpdate(stateManager.getState());

                // 在黯淡的背景下加载并显示其关系线
                await dataProcessor.streamAndAddNeighbors(nodeData.id, () => {});
                mainUpdate(stateManager.getState()); 

                // 等待1.5秒，让用户观察关系生长过程
                await new Promise(resolve => setTimeout(resolve, 1500));

                // 解除节点位置固定，让力导向图自然布局
                const finalNode = dataProcessor.nodeMap.get(nodeData.id);
                if (finalNode) {
                    finalNode.fx = null;
                    finalNode.fy = null;
                }
                
                // 恢复全局亮度，并执行最终的视角滑动和高亮
                graphView.undimGraph();
                if (finalNode) {
                    graphView.callbacks.onNodeClick({ stopPropagation: () => {} }, finalNode);
                    graphView.centerOnNode(finalNode);
                }
            }
            else {
                // --- 针对已存在节点的简化流程 ---
                const existingNode = dataProcessor.nodeMap.get(nodeData.id);
                if (existingNode) {
                    graphView.callbacks.onNodeClick({ stopPropagation: () => {} }, existingNode);
                    graphView.centerOnNode(existingNode);
                }
            }
        },
        onPathSearch: (sourceQuery, targetQuery, limit) => {
            // 1. 获取当前UI状态下真正可见的节点和关系
            const currentState = stateManager.getState();
            const { visibleNodes, validRels } = dataProcessor.getVisibleData(currentState);

            // 2. 调用findNode，它会先在visibleNodes中查找，如果找不到再从所有节点中查找
            const sourceResult = dataProcessor.findNode(sourceQuery, visibleNodes);
            const targetResult = dataProcessor.findNode(targetQuery, visibleNodes);

            // 3. 检查源节点，并根据findNode返回的isVisible标志给出精确错误提示
            if (!sourceResult.node) {
                uiController.showErrorToast(`源节点 "${sourceQuery}" 未加载。请先通过“节点搜索”加载它。`);
                return;
            }
            if (!sourceResult.isVisible) {
                uiController.showErrorToast(`源节点 "${sourceQuery}" 已加载，但被当前时间或图例过滤器隐藏。`);
                return;
            }

            // 4. 检查目标节点
            if (!targetResult.node) {
                uiController.showErrorToast(`目标节点 "${targetQuery}" 未加载。请先通过“节点搜索”加载它。`);
                return;
            }
            if (!targetResult.isVisible) {
                uiController.showErrorToast(`目标节点 "${targetQuery}" 已加载，但被当前时间或图例过滤器隐藏。`);
                return;
            }

            // 5. 确保使用过滤后的关系(validRels)进行路径查找
            const { paths } = dataProcessor.findPaths(sourceResult.node.id, targetResult.node.id, limit, validRels);
            
            if (paths.length > 0) {
                const pathNodeIds = Array.from(new Set(paths.flat()));
                stateManager.pinNodes(pathNodeIds, 'path');
                mainUpdate(stateManager.getState());

                stateManager.setPathHighlighting(true);
                
                setTimeout(() => {
                    const finalSourceNode = dataProcessor.nodeMap.get(sourceResult.node.id);
                    if(finalSourceNode) {
                        graphView.highlightPaths(paths, sourceResult.node.id, targetResult.node.id, finalSourceNode);
                        graphView.centerOnNode(finalSourceNode);
                    }
                }, 100);

                uiController._closeSearchPanel();
            }
            else {
                uiController.showErrorToast(`在当前的时间范围和过滤器下，未找到 "${sourceQuery}" 和 "${targetQuery}" 之间的路径。`);
            }
        }
    });
    
    try {
        await dataProcessor.loadData();
        graphView.updateLegend(dataProcessor.currentGraphData.nodes);
        uiController.initialize();
    } catch (error) {
        console.error('Failed to initialize graph:', error);
        document.getElementById("graph-container").innerHTML = `<h2>错误：无法加载关系图谱数据。</h2><p>详细错误: <code>${error.message}</code></p>`;
        return;
    }

    stateManager.subscribe(mainUpdate);
    
    // 手动调用一次 mainUpdate，以完成应用的首次渲染。
    mainUpdate(stateManager.getState());
});
