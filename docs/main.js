// docs/main.js

import { stateManager } from './modules/state.js';
import { DataProcessor } from './modules/dataProcessor.js';
import { GraphView } from './modules/graphView.js';
import { UIController } from './modules/uiController.js';
import { loadTranslations, t } from './modules/i18n.js';

const nodeFixTimers = new Map();

document.addEventListener('DOMContentLoaded', async () => {

    // --- 语言初始化 ---
    // 1. 从 localStorage 获取已保存的语言偏好
    const savedLang = localStorage.getItem('userLanguage');
    // 2. 如果没有，则根据浏览器语言决定默认语言
    const initialLang = savedLang || (navigator.language.toLowerCase().startsWith('zh') ? 'zh-cn' : 'en');
    
    // 3. 加载初始语言包
    await loadTranslations(initialLang);
    
    // 4. 将初始语言设置到全局状态中
    stateManager.initialize(initialLang);

    let lastLang = initialLang; // 用于检测语言变化

    const dataProcessor = new DataProcessor(() => mainUpdate(stateManager.getState()));


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

    const mainUpdate = async (currentState) => {
        // --- 监听并处理语言变化 ---
        if (currentState.language !== lastLang) {
            await loadTranslations(currentState.language);
            uiController._applyTranslations(); // 更新静态UI文本
            lastLang = currentState.language;
            // 语言变化后，图谱的重新渲染会自然发生，因为下面的代码会执行
        }

        const { visibleNodes, validRels, neighbors } = dataProcessor.getVisibleData(currentState);

        graphView.render({ visibleNodes, validRels });
        graphView.updateHighlights(currentState.selectedNodeId, neighbors);

        updateDestructionSchedules(currentState, neighbors);
    };

    const graphView = new GraphView('#graph-container', {
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

            // 更新Wiki链接框
            uiController.updateWikiLinks(isDeselecting ? null : d);
            
            if (isDeselecting) {
                // 如果是取消选中，也清理当前节点的定时器和固定状态
                clearTimeout(nodeFixTimers.get(d.id));
                nodeFixTimers.delete(d.id);
                d.fx = null;
                d.fy = null;
                stateManager.clearSelection();
                return; 
            }

            // 1. 先设置选中状态并立即手动触发一次更新以显示高亮
            stateManager.setSelectedNode(d.id);
            
            // 2. 异步加载数据
            const loadedData = await dataProcessor.streamAndAddNeighbors(d.id);

            // 3. 检查是否加载到了新数据
            if (loadedData && (loadedData.nodes.length > 0 || loadedData.links.length > 0)) {
                // 4. 将加载到的新数据手动添加到 dataProcessor
                const changed = dataProcessor._addNodesAndLinksToGraph(loadedData.nodes, loadedData.links);
                
                if (changed) {
                    // 5. 如果数据确实发生了变化，触发全局更新
                    graphView.updateLegend(dataProcessor.currentGraphData.nodes);
                    mainUpdate(stateManager.getState());
                }
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
            graphView.animationSourceNode = d;
            const graphChanged = await dataProcessor.streamAndAddNeighbors(d.id);

            if (graphChanged) {
                mainUpdate(stateManager.getState()); 
                graphView.updateLegend(dataProcessor.currentGraphData.nodes);
            }
        },
        onSvgClick: () => {
            uiController.hideWikiLinks();
            
            // 1. 立即停止任何正在进行的路径动画
            graphView.stopPathAnimation();
            // 2. 立即将图谱视觉效果恢复到默认状态
            graphView.clearAllHighlights();

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
        },
        onActiveNodeRemoved: (removedNodeId) => {
            const currentState = stateManager.getState();
            let shouldClear = false;

            // 情况一: 被移除的节点正是当前高亮的中心节点
            if (currentState.selectedNodeId === removedNodeId) {
                shouldClear = true;
            } 
            // 情况二: 被移除的节点是当前高亮路径的一部分
            else if (currentState.isPathHighlighting && currentState.pinnedNodeIds.get(removedNodeId) === 'path') {
                shouldClear = true;
                // 在清除高亮前，先解除路径节点的固定状态
                stateManager.unpinNodesByReason('path');
            }

            if (shouldClear) {
                graphView.stopPathAnimation();
                stateManager.clearSelection();
            }
        }
    });

    await graphView.init();

    setTimeout(() => {
        graphView.setInitialView();
    }, 100);

    const uiController = new UIController({
        onNodeSearch: async (query) => {
            graphView.hideTooltip();

            // 查找节点并检查其是否与当前过滤器兼容
            const nodeData = await dataProcessor.findAndLoadNodeData(query);
            if (!nodeData) {
                uiController.showErrorToast(t('error_node_not_exist', { query }));
                return;
            }

            const isCompatible = dataProcessor.isNodeCompatibleWithFilters(nodeData, stateManager.getState());
            if (!isCompatible) {
                const displayName = nodeData.name?.['zh-cn']?.[0] || nodeData.id;
                uiController.showErrorToast(t('error_node_hidden', { displayName, id: nodeData.id }));
                return;
            }

            const isNewNode = !dataProcessor.nodeMap.has(nodeData.id);

            // 如果是新节点，只需将其数据添加到核心数据结构中，并钉住。
            if (isNewNode) {
                dataProcessor._addNodesAndLinksToGraph([nodeData], []);
                stateManager.pinNodes([nodeData.id], 'click');
            }

            // 触发一次状态更新。这会调用 graphView.render，从而将新节点的创建任务“预约”到异步队列中。
            mainUpdate(stateManager.getState());

            setTimeout(() => {
                const node = dataProcessor.nodeMap.get(nodeData.id);
                if (!node) return; // 安全检查

                graphView.callbacks.onNodeClick({ stopPropagation: () => {} }, node);
                
                // 居中视图并关闭搜索面板。
                graphView.centerOnNode(node);
                uiController._closeSearchPanel();

            }, 100);
        },
        onPathSearch: (sourceQuery, targetQuery, limit) => {
            // 1. 获取当前UI状态下可见的节点和关系
            const currentState = stateManager.getState();
            const { visibleNodes, validRels } = dataProcessor.getVisibleData(currentState);

            // 2. 调用findNode，它会先在visibleNodes中查找，如果找不到再从所有节点中查找
            const sourceResult = dataProcessor.findNode(sourceQuery, visibleNodes);
            const targetResult = dataProcessor.findNode(targetQuery, visibleNodes);

            // 3. 检查源节点，并根据findNode返回的isVisible标志给出精确错误提示
            if (!sourceResult.node) {
                uiController.showErrorToast(t('error_path_source_not_loaded', { query: sourceQuery }));
                return;
            }
            if (!sourceResult.isVisible) {
                uiController.showErrorToast(t('error_path_source_hidden', { query: sourceQuery }));
                return;
            }

            // 4. 检查目标节点
            if (!targetResult.node) {
                uiController.showErrorToast(t('error_path_target_not_loaded', { query: targetQuery }));
                return;
            }
            if (!targetResult.isVisible) {
                uiController.showErrorToast(t('error_path_target_hidden', { query: targetQuery }));
                return;
            }

            // 5. 确保使用过滤后的关系(validRels)进行路径查找
            const result = dataProcessor.findPaths(sourceResult.node.id, targetResult.node.id, limit, validRels);
            const paths = result?.paths; // 使用可选链安全地获取路径
            
            // 增强检查：确保 paths 是一个数组且其长度大于0
            if (Array.isArray(paths) && paths.length > 0) {
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
                // 如果 paths 不存在或为空数组，则显示错误提示
                uiController.showErrorToast(t('error_path_not_found', { source: sourceQuery, target: targetQuery }));
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
