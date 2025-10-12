// docs/modules/state.js

import { addInterval, subtractInterval } from './utils.js';

// _state 存储应用的当前状态。它是私有的，只能通过 updateState 函数修改。
let _state = {};
// subscribers 存储所有订阅了状态变更的回调函数。
const subscribers = new Set();

/**
 * 内部函数，用于更新状态并通知所有订阅者。
 * 这是唯一可以修改 _state 的地方，确保了状态变更的可追溯性。
 * @param {object} newState 新的状态片段
 */
function updateState(newState) {
    _state = { ..._state, ...newState };
    subscribers.forEach(callback => callback(_state));
}

/**
 * stateManager 导出公共接口，用于管理应用状态。
 */
export const stateManager = {
    initialize: (initialLang = 'zh-cn') => {
        _state = {
            language: initialLang,
            startDate: getDateFromGroup('start'),
            endDate: getDateFromGroup('end'),
            hiddenTypes: new Set(),
            selectedNodeId: null,
            isIntervalLocked: false,
            currentInterval: { years: 0, months: 0, days: 0 },
            isPathHighlighting: false,
            // Pinned节点现在是一个Map，记录 { nodeId -> 'click' | 'path' }
            pinnedNodeIds: new Map(),
        };
    },
    
    getState: () => ({ ..._state }),

    /**
     * 订阅状态变更。
     * @param {Function} callback 当状态更新时要执行的回调函数
     * @returns {Function} 一个用于取消订阅的函数
     */
    subscribe: (callback) => {
        subscribers.add(callback);
        // 返回一个清理函数，允许组件在销毁时取消订阅
        return () => subscribers.delete(callback);
    },

    /**
     * 设置当前语言。
     * @param {string} lang 语言代码 ('zh-cn' or 'en')
     */
    setLanguage: (lang) => {
        if (_state.language !== lang) {
            updateState({ language: lang });
        }
    },

    /**
     * 更新开始和结束日期。
     * @param {Date} startDate
     * @param {Date} endDate
     */
    setDates: (startDate, endDate) => updateState({ startDate, endDate }),

    /**
     * 切换某个节点类型的可见性。
     * @param {string} type 节点类型
     * @param {boolean} isHidden 是否要隐藏
     */
    toggleHiddenType: (type, isHidden) => {
        const newHiddenTypes = new Set(_state.hiddenTypes);
        isHidden ? newHiddenTypes.add(type) : newHiddenTypes.delete(type);
        updateState({ hiddenTypes: newHiddenTypes });
    },
    
    /**
     * 设置当前选中的节点ID。
     * @param {string} nodeId
     */
    setSelectedNode: (nodeId) => updateState({ selectedNodeId: nodeId, isPathHighlighting: false }),

    /**
     * 清除所有选择和高亮状态。
     */
    clearSelection: () => updateState({ selectedNodeId: null, isPathHighlighting: false }),

    /**
     * 设置是否处于路径高亮模式。
     * @param {boolean} isHighlighting
     */
    setPathHighlighting: (isHighlighting) => updateState({ isPathHighlighting: isHighlighting }),

    /**
     * 将一组节点ID添加到“钉住”列表。
     * @param {Array<string>} nodeIds 要钉住的节点ID数组
     * @param {'click' | 'path'} reason 钉住的原因
     */
    pinNodes: (nodeIds, reason = 'click') => {
        const newPinnedIds = new Map(_state.pinnedNodeIds);
        nodeIds.forEach(id => {
            // "click" 的优先级更高，一旦被点击过，就不应被 "path" 覆盖
            if (newPinnedIds.get(id) !== 'click') {
                newPinnedIds.set(id, reason);
            }
        });
        updateState({ pinnedNodeIds: newPinnedIds });
    },
    
    /**
     * 从“钉住”列表中移除因为特定原因而被固定的节点。
     * @param {'click' | 'path'} reason 要移除的原因
     */
    unpinNodesByReason: (reason) => {
        const newPinnedIds = new Map();
        for (const [id, r] of _state.pinnedNodeIds.entries()) {
            if (r !== reason) {
                newPinnedIds.set(id, r);
            }
        }
        updateState({ pinnedNodeIds: newPinnedIds });
    },

    setIntervalLock: (isLocked, interval = { years: 0, months: 0, days: 0 }) => {
        updateState({ isIntervalLocked: isLocked, currentInterval: interval });
        // 如果是锁定操作，立即根据开始日期和间隔计算结束日期并更新
        if (isLocked) {
            const newEndDate = addInterval(_state.startDate, interval);
            updateDateGroup('end', newEndDate);
            updateState({ endDate: newEndDate });
        }
    },

    /**
     * 当时间间隔锁定时，根据一个日期的变动来同步另一个日期。
     * @param {'start' | 'end'} originPrefix 变动的日期是开始还是结束
     */
    propagateIntervalChange: (originPrefix) => {
        if (!_state.isIntervalLocked) return;
        const originDate = getDateFromGroup(originPrefix);
        if (!originDate) return;

        if (originPrefix === 'start') {
            const newEndDate = addInterval(originDate, _state.currentInterval);
            updateDateGroup('end', newEndDate);
            updateState({ startDate: originDate, endDate: newEndDate });
        }
        else {
            const newStartDate = subtractInterval(originDate, _state.currentInterval);
            updateDateGroup('start', newStartDate);
            updateState({ startDate: newStartDate, endDate: originDate });
        }
    }
};

/**
 * 从DOM中读取日期输入框组的值并返回一个Date对象。
 * @param {string} prefix 'start' 或 'end'
 * @returns {Date | null}
 */
export function getDateFromGroup(prefix) {
    const year = document.getElementById(`${prefix}-year`).value;
    const month = document.getElementById(`${prefix}-month`).value;
    const day = document.getElementById(`${prefix}-day`).value;
    if (!year || !month || !day) return null;
    const date = new Date(year, month - 1, day);
    // 验证日期是否有效 (e.g., month > 12)
    return (date.getFullYear() != year || date.getMonth() + 1 != month || date.getDate() != day) ? null : date;
}

/**
 * 将一个Date对象的值更新到DOM的日期输入框组中。
 * @param {string} prefix 'start' 或 'end'
 * @param {Date} date
 */
export function updateDateGroup(prefix, date) {
    document.getElementById(`${prefix}-year`).value = date.getFullYear();
    document.getElementById(`${prefix}-month`).value = String(date.getMonth() + 1);
    document.getElementById(`${prefix}-day`).value = String(date.getDate());
}
