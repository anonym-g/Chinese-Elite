// docs/modules/config.js

/**
 * 全局配置文件
 * 存放应用中所有硬编码的常量和配置，便于集中管理和修改。
 */
const CONFIG = {
    // 数据源文件的URL
    DATA_FILE_URL: './consolidated_graph.json',

    // D3.js力导向图的模拟参数
    SIMULATION: {
        CHARGE_STRENGTH: -250,      // 节点间的引力/斥力强度
        LINK_DISTANCE: 100,         // 边的理想长度
        CENTER_X_STRENGTH: 0.01,    // 将所有节点拉向中心的X轴方向的力强度
        CENTER_Y_STRENGTH: 0.01,    // 将所有节点拉向中心的Y轴方向的力强度
        ANCHOR_STRENGTH: 0.3        // "锚点"节点（度数最高的节点）被拉向中心的力强度，使其更稳定
    },

    // 节点半径的计算参数
    NODE_RADIUS: {
        BASE: 5,                    // 节点的基础半径
        SCALE: 2                    // 节点半径随其度数变化的缩放因子
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
        'SIBLING_OF', 
        'LOVER_OF', 
        'RELATIVE_OF', 
        'FRIEND_OF', 
        'ENEMY_OF', 
        'MET_WITH'
    ])
};

export default CONFIG;
