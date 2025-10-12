// docs/modules/uiController.js

import { debounce, getAdjacentInput } from './utils.js';
import { stateManager, getDateFromGroup, updateDateGroup } from './state.js';
import { t, loadTranslations } from './i18n.js';

/**
 * UIController 类负责管理所有的DOM元素和用户交互事件。
 * 它监听用户操作，然后调用回调函数来通知其他模块执行相应的逻辑。
 */
export class UIController {
    constructor(callbacks) {
        // 回调函数，用于通知 main.js 执行搜索等业务逻辑
        this.callbacks = callbacks; // { onNodeSearch, onPathSearch }
        
        // --- 查询并缓存所有需要的DOM元素 ---
        this.searchTriggerContainer = document.getElementById('search-trigger-container');
        this.searchToggleButton = document.getElementById('search-toggle-btn');
        this.searchModeSelector = document.getElementById('search-mode-selector');
        this.searchModeCurrent = document.getElementById('search-mode-current');
        this.searchModeOptions = document.getElementById('search-mode-options');
        this.searchInputPanel = document.getElementById('search-input-panel');
        this.legendWrapper = document.querySelector('.legend-wrapper');
        this.errorToast = document.getElementById('error-toast');
        this.errorToastMessage = document.getElementById('error-toast-message');

        this.languageToggleButton = document.getElementById('language-toggle-btn');
        this.languageOptions = document.getElementById('language-options');
        
        // 内部UI状态
        this.uiState = {
            isSearchActive: false,
            isSearchPanelOpen: false,
            searchMode: 'node'
        };
    }

    /**
     * 绑定所有UI事件监听器。
     */
    initialize() {
        this._applyTranslations();

        this.languageToggleButton.addEventListener('click', () => this._toggleLanguageOptions());
        this.languageOptions.querySelectorAll('li').forEach(li => {
            li.addEventListener('click', (e) => this._handleLanguageSelect(e));
        });

        // 点击页面其他地方，关闭语言选项卡
        document.addEventListener('click', (e) => {
            if (!this.languageToggleButton.contains(e.target) && !this.languageOptions.contains(e.target)) {
                this.languageOptions.classList.add('collapsed');
            }
        });

        // --- 日期控制器事件 ---
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
                this.debouncedDateUpdate(e.target.id.split('-')[0]);
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

        // --- 时间间隔控制器事件 ---
        document.getElementById('toggle-interval-btn').addEventListener('click', e => {
            e.preventDefault();
            document.getElementById('interval-inputs-wrapper').classList.toggle('hidden');
        });
        document.getElementById('set-interval-btn').addEventListener('click', () => this._handleSetInterval());
        document.getElementById('clear-interval-btn').addEventListener('click', () => this._handleClearInterval());

        // --- 搜索面板事件 ---
        this.searchToggleButton.addEventListener('click', () => this._handleSearchToggleClick());
        this.searchModeCurrent.addEventListener('click', () => this._toggleSearchModeOptions());
        this.searchModeOptions.querySelectorAll('li').forEach(li => {
            li.addEventListener('click', (e) => this._handleSearchModeSelect(e));
        });
        document.getElementById('node-search-submit').addEventListener('click', () => this._handleNodeSearch());
        document.getElementById('path-search-submit').addEventListener('click', () => this._handlePathSearch());
        document.getElementById('node-search-input').addEventListener('keydown', (e) => {
            if (e.key === 'Enter') this._handleNodeSearch();
        });
        document.getElementById('path-target-input').addEventListener('keydown', (e) => {
            if (e.key === 'Enter') this._handlePathSearch();
        });
        document.getElementById('search-panel-close-btn').addEventListener('click', () => this._closeSearchPanel());

        // --- 错误提示框事件 ---
        document.getElementById('error-toast-close').addEventListener('click', () => this._handleErrorToastClose());

        // --- 监听图例大小变化以调整搜索面板位置 ---
        this.legendObserver = new ResizeObserver(() => {
            this._updateSearchPanelPosition();
        });
        this.legendObserver.observe(this.legendWrapper);
    }

    _toggleLanguageOptions() {
        this.languageOptions.classList.toggle('collapsed');
    }

    _handleLanguageSelect(event) {
        const selectedLang = event.target.dataset.lang;
        if (selectedLang) {
            // 保存用户选择到 localStorage 以实现持久化
            localStorage.setItem('userLanguage', selectedLang);
            this._handleLanguageChange(selectedLang);
        }
        this.languageOptions.classList.add('collapsed');
    }

    async _handleLanguageChange(lang) {
        stateManager.setLanguage(lang);
    }

    _applyTranslations() {
        document.querySelector('h1').textContent = t('appTitle');
        document.querySelector('#search-mode-options li[data-mode="node"]').textContent = t('search_node');
        document.querySelector('#search-mode-options li[data-mode="path"]').textContent = t('search_path');
        
        const currentMode = this.uiState.searchMode === 'node' ? t('search_node') : t('search_path');
        this.searchModeCurrent.querySelector('span').textContent = currentMode;

        document.getElementById('node-search-input').placeholder = t('search_node_placeholder');
        document.getElementById('node-search-submit').textContent = t('search_button');
        document.getElementById('path-source-input').placeholder = t('search_path_source_placeholder');
        document.getElementById('path-target-input').placeholder = t('search_path_target_placeholder');
        document.getElementById('path-limit-input').title = t('path_limit_title');
        document.getElementById('path-search-submit').textContent = t('search_button');
        
        document.querySelector('label[for="start-date-group"]').textContent = t('start_date');
        document.querySelector('label[for="end-date-group"]').textContent = t('end_date');
        document.getElementById('toggle-interval-btn').textContent = t('set_interval');
        document.getElementById('clear-interval-btn').textContent = t('clear_limit');
        document.getElementById('interval-year').placeholder = t('interval_year_placeholder');
        document.getElementById('interval-month').placeholder = t('interval_month_placeholder');
        document.getElementById('interval-day').placeholder = t('interval_day_placeholder');
        document.getElementById('set-interval-btn').textContent = t('set_button');
    }
    
    // 使用防抖来处理连续的日期输入
    debouncedDateUpdate = debounce((prefix) => {
        const isLocked = stateManager.getState().isIntervalLocked;
        if (isLocked) {
            stateManager.propagateIntervalChange(prefix);
        }
        else {
            stateManager.setDates(getDateFromGroup('start'), getDateFromGroup('end'));
        }
    }, 300);

    /**
     * 显示错误提示框。
     * @param {string} message - 要显示的消息
     */
    showErrorToast(message) {
        this.errorToastMessage.textContent = message;
        this.errorToast.classList.remove('hidden', 'closing');
        if (this.errorToast.closeTimer) clearTimeout(this.errorToast.closeTimer);
        this.errorToast.closeTimer = setTimeout(() => this._handleErrorToastClose(), 5000);
    }

    // --- "私有" UI事件处理方法 ---

    _modifyDate(prefix, part, direction) {
        const currentDate = getDateFromGroup(prefix) || new Date();
        if (isNaN(currentDate.getTime())) return;

        switch (part) {
            case 'year': currentDate.setFullYear(currentDate.getFullYear() + direction); break;
            case 'month': currentDate.setMonth(currentDate.getMonth() + direction); break;
            case 'day': currentDate.setDate(currentDate.getDate() + direction); break;
        }
        updateDateGroup(prefix, currentDate);
        
        // 触发状态更新
        this.debouncedDateUpdate(prefix);
    }

    _handleDatePartKeydown(event) {
        const target = event.target;
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
        }
        else if (event.key === 'ArrowRight' && isAtEnd) {
            const nextInput = getAdjacentInput(prefix, part, 'next');
            if (nextInput) {
                event.preventDefault();
                nextInput.focus();
            }
        }
        if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
            event.preventDefault();
            const direction = (event.key === 'ArrowUp') ? 1 : -1;
            this._modifyDate(prefix, part, direction);
        }
    }
    
    _handleSetInterval() {
        const years = parseInt(document.getElementById('interval-year').value) || 0;
        const months = parseInt(document.getElementById('interval-month').value) || 0;
        const days = parseInt(document.getElementById('interval-day').value) || 0;
        stateManager.setIntervalLock(true, { years, months, days });
        document.getElementById('clear-interval-btn').classList.remove('hidden');
        document.getElementById('interval-inputs-wrapper').classList.add('hidden');
    }

    _handleClearInterval() {
        stateManager.setIntervalLock(false);
        document.getElementById('clear-interval-btn').classList.add('hidden');
    }

    _handleSearchToggleClick() {
        this.uiState.isSearchActive = !this.uiState.isSearchActive;
        this.searchTriggerContainer.classList.toggle('active', this.uiState.isSearchActive);
        this.searchToggleButton.classList.toggle('active', this.uiState.isSearchActive);
        
        if (!this.uiState.isSearchActive) {
            this._closeSearchPanel();
            this.searchModeOptions.classList.add('collapsed');
            this.searchModeCurrent.classList.remove('open');
        }
        else {
            this._openSearchPanel();
        }
    }
    
    _toggleSearchModeOptions() {
        const isCollapsed = this.searchModeOptions.classList.toggle('collapsed');
        this.searchModeCurrent.classList.toggle('open', !isCollapsed);
        this.searchModeCurrent.setAttribute('aria-expanded', !isCollapsed);
    }
    
    _handleSearchModeSelect(event) {
        const selectedMode = event.target.dataset.mode;
        if (selectedMode && this.uiState.searchMode !== selectedMode) {
            this.uiState.searchMode = selectedMode;
            this.searchModeCurrent.querySelector('span').textContent = event.target.textContent;
            document.querySelectorAll('.search-content').forEach(c => c.classList.remove('active'));
            document.getElementById(`${selectedMode}-search-content`).classList.add('active');
        }
        this._toggleSearchModeOptions();
        this._openSearchPanel();
    }
    
    _openSearchPanel() {
        if (this.uiState.isSearchPanelOpen) return;
        this.uiState.isSearchPanelOpen = true;
        this._updateSearchPanelPosition();
        this.searchInputPanel.classList.remove('collapsed');
    }

    _closeSearchPanel() {
        if (!this.uiState.isSearchPanelOpen) return;
        this.uiState.isSearchPanelOpen = false;
        this.searchInputPanel.classList.add('collapsed');
    }

    _updateSearchPanelPosition() {
        const legendRect = this.legendWrapper.getBoundingClientRect();
        this.searchInputPanel.style.top = `${legendRect.bottom}px`;
    }

    _handleNodeSearch() {
        const query = document.getElementById('node-search-input').value.trim();
        if (query) this.callbacks.onNodeSearch(query);
    }

    _handlePathSearch() {
        const sourceQuery = document.getElementById('path-source-input').value.trim();
        const targetQuery = document.getElementById('path-target-input').value.trim();
        const limit = parseInt(document.getElementById('path-limit-input').value, 10);

        if (!sourceQuery || !targetQuery) {
            this.showErrorToast("源节点和目标节点均不能为空。");
            return;
        }
        this.callbacks.onPathSearch(sourceQuery, targetQuery, limit);
    }
    
    _handleErrorToastClose() {
        if (this.errorToast.closeTimer) clearTimeout(this.errorToast.closeTimer);
        this.errorToast.classList.add('closing');
        setTimeout(() => {
            this.errorToast.classList.add('hidden');
            this.errorToast.classList.remove('closing');
        }, 500);
    }
}
