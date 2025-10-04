// docs/modules/config.js

/**
 * 全局配置文件
 */
const CONFIG = {
    // DATA_FILE_URL: './data/initial.json',
    DATA_FILE_URL: './master_graph_qcode.json',
    DATA_DIR: './data/',
    
    // 全局名称到ID的映射文件
    NAME_TO_ID_URL: './data/name_to_id.json',

    TEMPORARY_NODE_TTL: 60000, // 1分钟

    INITIAL_ZOOM: 0.7, // 初始摄像机缩放级别（小于1表示拉远）
    INITIAL_ZOOM_DURATION: 2000, // 初始视图动画的持续时间（毫秒）

    SIMULATION: {
        INITIAL_ALPHA: 0.5, // 首次加载时的初始“能量”，1.0为最大值
        REHEAT_ALPHA: 0.3,  // 后续更新时的“能量”
        CHARGE_STRENGTH: -250,
        LINK_DISTANCE: 100,
        CENTER_X_STRENGTH: 0.01,
        CENTER_Y_STRENGTH: 0.01,
        ANCHOR_STRENGTH: 0.4 // 锚定力（加给度最高的3个节点）
    },

    // 节点半径的计算参数
    NODE_RADIUS: {
        BASE: 5,
        SCALE: 2
    },

    // 颜色配置
    COLORS: {
        DEFAULT_LINK: '#fff',       // 默认的边颜色
        DEFAULT_ARROW: '#999',      // 默认的箭头颜色
        NODE_TYPES: {               // 不同类型节点的颜色映射
            'Movement': 'rgb(0, 255, 255)',
            'Person': 'rgb(255, 0, 0)',
            'Organization': 'rgb(25, 40, 200)',
            'Event': 'rgb(170, 30, 170)',
            'Location': 'rgb(225, 170, 30)',
            'Document': 'rgb(15, 255, 50)'
        }
    },

    // 定义哪些关系类型是无向的（渲染时不需要箭头）
    NON_DIRECTED_LINK_TYPES: new Set([
        'SPOUSE_OF',
        'SIBLING_OF', 
        'LOVER_OF', 
        'RELATIVE_OF', 
        'FRIEND_OF', 
        'ENEMY_OF', 
        'MET_WITH'
    ])
};

export default CONFIG;
