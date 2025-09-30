// docs/main.js
import { stateManager } from './modules/state.js';
import { DataProcessor } from './modules/dataProcessor.js';
import { GraphView } from './modules/graphView.js';
import { UIController } from './modules/uiController.js';

/**
 * 应用主入口
 * 当DOM加载完成后执行
 */
document.addEventListener('DOMContentLoaded', async () => {
    
    // --- 1. 初始化模块 ---

    // 初始化状态管理器
    stateManager.initialize();

    // 初始化数据处理器
    const dataProcessor = new DataProcessor();

    // 初始化视图模块 (GraphView)，并传入回调函数
    const graphView = new GraphView('#graph', {
        onNodeClick: (event, d) => {
            event.stopPropagation();
            const currentState = stateManager.getState();
            // 如果点击的是已选中的节点，则取消选择；否则选择新节点
            currentState.selectedNodeId === d.id ? stateManager.clearSelection() : stateManager.setSelectedNode(d.id);
        },
        onSvgClick: () => {
            stateManager.clearSelection();
        },
        onLegendToggle: (event, type) => {
            // 当图例复选框变化时，更新状态
            stateManager.toggleHiddenType(type, !event.target.checked);
        },
        onColorChange: () => {
             // 当颜色选择器变化时，强制触发一次重绘
             mainUpdate(stateManager.getState());
        }
    });

    // 初始化UI控制器模块 (UIController)，并传入回调函数
    const uiController = new UIController({
        onNodeSearch: (query) => {
            const state = stateManager.getState();
            // 获取当前可见的节点数据，用于搜索
            const { visibleNodes } = dataProcessor.getVisibleData(state);
            const { node, isVisible } = dataProcessor.findNode(query, visibleNodes);

            if (node && isVisible) {
                stateManager.setSelectedNode(node.id);
                graphView.centerOnNode(node);
                uiController._closeSearchPanel();
            } else if (node && !isVisible) {
                uiController.showErrorToast(`节点 "${query}" (${node.id}) 存在，但当前未被显示。请检查图例或时间范围。`);
            } else {
                uiController.showErrorToast(`节点 "${query}" 不存在。`);
            }
        },
        onPathSearch: (sourceQuery, targetQuery, limit) => {
            const state = stateManager.getState();
            const { visibleNodes, validRels } = dataProcessor.getVisibleData(state);
            const sourceResult = dataProcessor.findNode(sourceQuery, visibleNodes);
            const targetResult = dataProcessor.findNode(targetQuery, visibleNodes);

            // 详细的节点存在性和可见性检查
            if (!sourceResult.node) { uiController.showErrorToast(`源节点 "${sourceQuery}" 不存在。`); return; }
            if (!sourceResult.isVisible) { uiController.showErrorToast(`源节点 "${sourceQuery}" (${sourceResult.node.id}) 存在但当前不可见。`); return; }
            if (!targetResult.node) { uiController.showErrorToast(`目标节点 "${targetQuery}" 不存在。`); return; }
            if (!targetResult.isVisible) { uiController.showErrorToast(`目标节点 "${targetQuery}" (${targetResult.node.id}) 存在但当前不可见。`); return; }

            const paths = dataProcessor.findPaths(sourceResult.node.id, targetResult.node.id, limit, validRels);
            if (paths.length > 0) {
                 stateManager.setPathHighlighting(true);
                 graphView.highlightPaths(paths, sourceResult.node.id, targetResult.node.id, sourceResult.node);
                 graphView.centerOnNode(sourceResult.node);
                 uiController._closeSearchPanel();
            } else {
                 uiController.showErrorToast(`在 "${sourceQuery}" 和 "${targetQuery}" 之间未找到路径。`);
            }
        }
    });
    
    // --- 2. 加载数据并初始化依赖于数据的部分 ---
    
    try {
        await dataProcessor.loadData();
        // 数据加载成功后，创建图例并初始化UI控制器
        graphView.createLegend(dataProcessor.fullGraphData.nodes);
        uiController.initialize();
    } catch (error) {
        console.error('Failed to initialize graph:', error);
        document.getElementById("graph-container").innerHTML = `<h2>错误：无法加载关系图谱数据。</h2><p>详细错误: <code>${error.message}</code></p>`;
        return; // 加载失败，终止执行
    }

    // --- 3. 定义核心更新流程 ---
    
    /**
     * 主更新函数。每当状态改变时，此函数将被调用。
     * 它的职责是：根据最新状态从数据处理器获取可见数据，然后将数据交给视图进行渲染。
     * @param {object} currentState - 最新的应用状态
     */
    const mainUpdate = (currentState) => {
        const { visibleNodes, validRels, neighbors } = dataProcessor.getVisibleData(currentState);
        graphView.render({ visibleNodes, validRels });
        graphView.updateHighlights(currentState.selectedNodeId, neighbors);
    };

    // --- 4. 建立响应式数据流 ---

    // 订阅状态变更。当 stateManager 中的状态更新时，自动调用 mainUpdate。
    stateManager.subscribe(mainUpdate);

    // --- 5. 首次渲染 ---
    
    // 手动调用一次 mainUpdate，以完成应用的首次渲染。
    mainUpdate(stateManager.getState());
});
