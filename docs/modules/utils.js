// docs/modules/utils.js

/**
 * 防抖函数。在事件触发后等待指定时间再执行，如果期间再次触发，则重新计时。
 * @param {Function} func 需要防抖的函数
 * @param {number} delay 延迟毫秒数
 * @returns {Function} 防抖处理后的函数
 */
export function debounce(func, delay) {
    let timeout;
    return (...args) => {
        clearTimeout(timeout);
        timeout = setTimeout(() => func.apply(this, args), delay);
    };
}

/**
 * 解析日期字符串。支持 YYYY, YYYY-MM, YYYY-MM-DD 格式。
 * @param {string | null} dateStr 日期字符串
 * @returns {Date | null} 解析后的Date对象，若无效则返回null
 */
export function parseDate(dateStr) {
    if (!dateStr || typeof dateStr !== 'string') return null;
    // 兼容 YYYY-MM-DD, YYYY-M-D 等格式
    if (/^\d{4}$/.test(dateStr)) return new Date(`${dateStr}-01-01T00:00:00`);
    if (/^\d{4}-\d{1,2}$/.test(dateStr)) return new Date(`${dateStr}-01T00:00:00`);
    const date = new Date(`${dateStr}T00:00:00`);
    return isNaN(date.getTime()) ? null : date;
}

/**
 * 将模糊的日期范围扩展到其区间的最后一天。
 * 例如 "1999" -> "1999-12-31", "1999-02" -> "1999-02-28"
 * @param {string} originalStr 原始日期字符串
 * @param {Date} parsedDate 已解析的Date对象
 * @returns {Date} 扩展后的Date对象
 */
export function expandVagueDate(originalStr, parsedDate) {
    if (!originalStr || !parsedDate) return parsedDate;
    const trimmedStr = originalStr.trim();
    if (/^\d{4}$/.test(trimmedStr)) { // 如果是年份
        const endOfYear = new Date(parsedDate);
        endOfYear.setFullYear(endOfYear.getFullYear() + 1);
        endOfYear.setDate(endOfYear.getDate() - 1);
        return endOfYear;
    }
    else if (/^\d{4}-\d{1,2}$/.test(trimmedStr)) { // 如果是年月
        const endOfMonth = new Date(parsedDate);
        endOfMonth.setMonth(endOfMonth.getMonth() + 1);
        endOfMonth.setDate(endOfMonth.getDate() - 1);
        return endOfMonth;
    }
    return parsedDate;
}

/**
 * 在给定日期上增加一个时间间隔。
 * @param {Date} date 原始日期
 * @param {{years: number, months: number, days: number}} interval 时间间隔对象
 * @returns {Date} 计算后的新日期
 */
export function addInterval(date, interval) {
    const newDate = new Date(date);
    if (interval.years) newDate.setFullYear(newDate.getFullYear() + interval.years);
    if (interval.months) newDate.setMonth(newDate.getMonth() + interval.months);
    if (interval.days) newDate.setDate(newDate.getDate() + interval.days);
    return newDate;
}

/**
 * 在给定日期上减去一个时间间隔。
 * @param {Date} date 原始日期
 * @param {{years: number, months: number, days: number}} interval 时间间隔对象
 * @returns {Date} 计算后的新日期
 */
export function subtractInterval(date, interval) {
    const newDate = new Date(date);
    // 注意减法的顺序，以避免月份天数问题
    if (interval.days) newDate.setDate(newDate.getDate() - interval.days);
    if (interval.months) newDate.setMonth(newDate.getMonth() - interval.months);
    if (interval.years) newDate.setFullYear(newDate.getFullYear() - interval.years);
    return newDate;
}

/**
 * 获取日期输入框组中相邻的输入框。用于方向键导航。
 * @param {string} prefix 'start' 或 'end'
 * @param {string} part 'year', 'month', 或 'day'
 * @param {'next' | 'prev'} direction 导航方向
 * @returns {HTMLElement | null} 相邻的输入框元素
 */
export function getAdjacentInput(prefix, part, direction) {
    const order = ['year', 'month', 'day'];
    const currentIndex = order.indexOf(part);
    const nextIndex = direction === 'next' ? currentIndex + 1 : currentIndex - 1;
    if (nextIndex >= 0 && nextIndex < order.length) {
        return document.getElementById(`${prefix}-${order[nextIndex]}`);
    }
    return null;
}

/**
 * 将 'rgb(r, g, b)' 格式的颜色字符串转换为 '#rrggbb' 格式。
 * @param {string} rgb RGB颜色字符串
 * @returns {string} HEX颜色字符串
 */
export function rgbToHex(rgb) {
    if (!rgb || !rgb.startsWith('rgb')) return '#000000';
    const match = rgb.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
    if (!match) return '#000000';
    const toHex = (c) => ('0' + parseInt(c, 10).toString(16)).slice(-2);
    return `#${toHex(match[1])}${toHex(match[2])}${toHex(match[3])}`;
}

export function easeInOutQuad(t) {
    return t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
}
