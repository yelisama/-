(function() {
    function build(p) { return "page/" + String(p).replace(/^\/+/, ""); }

    // 前端兜底：奖池与配置常量
    var PRIZE_POOL = [
        {name: "★金光闪闪大奖★", prob: 0.01, range: "80~150", color: "#e6b422"},
        {name: "二等奖", prob: 0.05, range: "30~50", color: "#a0a0a0"},
        {name: "三等奖", prob: 0.14, range: "15~25", color: "#cd7f32"},
        {name: "四等奖", prob: 0.30, range: "8~14", color: "#4a9eff"},
        {name: "参与奖", prob: 0.50, range: "1~7", color: "#3ecf8e"},
    ];

    var CONSTANTS = [
        {k: "单抽花费", v: "10 金币"},
        {k: "十连花费", v: "100 金币"},
        {k: "许愿花费", v: "500 金币"},
        {k: "每日抽卡上限", v: "10 次"},
        {k: "收藏品保底", v: "90 抽"},
        {k: "参与奖掉率", v: "0.5%"},
        {k: "新人初始金币", v: "200"},
        {k: "每日投喂上限", v: "3 次"},
        {k: "投喂金币范围", v: "-30 ~ +30"},
    ];

    var CMDS = [
        {cmd: "/刮刮乐", desc: "单抽一次（10金币）"},
        {cmd: "/十连", desc: "十连抽（100金币）"},
        {cmd: "/金币", desc: "查看余额"},
        {cmd: "/收藏品", desc: "查看收藏柜"},
        {cmd: "/投喂 <物品>", desc: "投喂换金币（日限3次）"},
        {cmd: "/许愿 <内容>", desc: "花500金币挂愿望"},
        {cmd: "/许愿树", desc: "查看所有愿望"},
        {cmd: "/刮刮乐帮助", desc: "显示完整帮助"},
    ];

    function $(id) { return document.getElementById(id); }
    function esc(t) { var d = document.createElement('div'); d.textContent = t; return d.innerHTML; }

    var bridge = window.AstrBotPluginPage;
    function build(p) { return "page/" + String(p).replace(/^\/+/, ""); }

    async function apiGet(ep) {
        if (!bridge) throw new Error("桥接未就绪");
        return await bridge.apiGet(build(ep), {});
    }

    // ========== Tab ==========
    function initTabs() {
        document.querySelectorAll('.nav-btn').forEach(function(btn) {
            btn.addEventListener('click', function() {
                var tab = this.dataset.tab;
                document.querySelectorAll('.nav-btn').forEach(function(b) { b.classList.remove('active'); });
                document.querySelectorAll('.tab-panel').forEach(function(p) { p.classList.remove('active'); });
                this.classList.add('active');
                $('tab-' + tab).classList.add('active');

                if (tab === 'dashboard') loadDashboard();
                else if (tab === 'users') loadUsers();
                else if (tab === 'leaderboard') loadLeaderboard();
                else if (tab === 'wishes') loadWishes();
                            });
        });
    }

    // ========== Dashboard ==========
    async function loadDashboard() {
        var kpi = $('kpi-row');
        kpi.innerHTML = '<div class="kpi"><div class="kpi-value">-</div><div class="kpi-label">加载中...</div></div>';

        try {
            var stats = await apiGet("stats");
            // 尝试获取今日数据
            var today = { active_users: 0, today_draws: 0, today_wishes: 0, today_feeds: 0 };
            try { today = await apiGet("today"); } catch(e) {}

            var items = [
                {label: "总用户数", value: stats.total_users || 0},
                {label: "总抽卡数", value: stats.total_draws || 0},
                {label: "总愿望数", value: stats.total_wishes || 0},
                {label: "总收藏品", value: stats.total_collectibles || 0},
                {label: "今日活跃", value: today.active_users || 0},
                {label: "今日抽卡", value: today.today_draws || 0},
            ];

            kpi.innerHTML = '';
            items.forEach(function(it) {
                var div = document.createElement('div');
                div.className = 'kpi';
                div.innerHTML = '<div class="kpi-value">' + it.value.toLocaleString() + '</div><div class="kpi-label">' + it.label + '</div>';
                kpi.appendChild(div);
            });
        } catch(e) {
            kpi.innerHTML = '<div class="error">加载失败: ' + esc(e.message) + '</div>';
        }

        // 奖池配置
        var pc = $('prize-config');
        pc.innerHTML = '';
        PRIZE_POOL.forEach(function(p) {
            var row = document.createElement('div');
            row.className = 'prize-row';
            row.innerHTML =
                '<div class="prize-tag">' + p.name + '</div>' +
                '<div class="prize-bar-wrap"><div class="prize-bar" style="width:' + (p.prob*100) + '%;background:' + p.color + '">' + (p.prob*100).toFixed(0) + '%</div></div>' +
                '<div class="prize-range">' + p.range + '</div>';
            pc.appendChild(row);
        });

        // 常量
        var cl = $('const-list');
        cl.innerHTML = '';
        CONSTANTS.forEach(function(c) {
            var div = document.createElement('div');
            div.className = 'const-item';
            div.innerHTML = '<span class="const-key">' + c.k + '</span><span class="const-val">' + c.v + '</span>';
            cl.appendChild(div);
        });

        // 指令
        var cg = $('cmd-grid');
        cg.innerHTML = '';
        CMDS.forEach(function(c) {
            var div = document.createElement('div');
            div.className = 'cmd-item';
            div.innerHTML = '<span class="cmd-name">' + c.cmd + '</span><span class="cmd-desc">' + c.desc + '</span>';
            cg.appendChild(div);
        });
    }

    // ========== Users ==========
    var allUsers = [];

    async function loadUsers() {
        var tbody = $('users-body');
        tbody.innerHTML = '<tr><td colspan="9" class="empty">加载中...</td></tr>';

        try {
            var resp = await apiGet("users");
            allUsers = resp.users || [];
            renderUsers();
        } catch(e) {
            tbody.innerHTML = '<tr><td colspan="9" class="error">加载失败: ' + esc(e.message) + '</td></tr>';
        }

        $('user-search').oninput = function() { renderUsers(); };
        $('user-sort').onchange = function() { renderUsers(); };
    }

    function renderUsers() {
        var q = $('user-search').value.trim().toLowerCase();
        var sort = $('user-sort').value;
        var list = allUsers.filter(function(u) {
            return !q || (u.user_id || '').toLowerCase().includes(q);
        });

        list.sort(function(a, b) {
            if (sort === 'coins_desc') return (b.coins || 0) - (a.coins || 0);
            if (sort === 'draws_desc') return (b.total_draws || 0) - (a.total_draws || 0);
            if (sort === 'collection_desc') return (b.collection_count || 0) - (a.collection_count || 0);
            if (sort === 'active_desc') return (b.daily_draws || 0) - (a.daily_draws || 0);
            return 0;
        });

        $('user-count').textContent = '共 ' + list.length + ' 人';

        var tbody = $('users-body');
        if (!list.length) {
            tbody.innerHTML = '<tr><td colspan="9" class="empty">无匹配用户</td></tr>';
            return;
        }

        tbody.innerHTML = '';
        list.forEach(function(u) {
            var pity = Math.max(0, 90 - (u.total_draws || 0));
            var tr = document.createElement('tr');
            tr.innerHTML =
                '<td><code>' + esc(u.user_id) + '</code></td>' +
                '<td><span class="badge-pill badge-gold">' + (u.coins || 0) + '</span></td>' +
                '<td>' + (u.total_draws || 0) + '</td>' +
                '<td>' + (u.daily_draws || 0) + '/10</td>' +
                '<td>' + (u.collection_count || 0) + '</td>' +
                '<td>' + (u.wish_count || 0) + '</td>' +
                '<td>' + (u.feed_count || 0) + '/3</td>' +
                '<td>' + pity + '</td>' +
                '<td><button class="btn" data-uid="' + esc(u.user_id) + '">详情</button></td>';
            tbody.appendChild(tr);
        });

        tbody.querySelectorAll('.btn').forEach(function(btn) {
            btn.addEventListener('click', function() { openUserModal(this.dataset.uid); });
        });
    }

    // ========== Leaderboard ==========
    async function loadLeaderboard() {
        var tbody = $('leaderboard-body');
        tbody.innerHTML = '<tr><td colspan="5" class="empty">加载中...</td></tr>';
        var metric = $('leaderboard-metric').value;

        try {
            var resp = await apiGet("top?metric=" + metric);
            var users = resp.users || [];
            tbody.innerHTML = '';

            if (!users.length) {
                tbody.innerHTML = '<tr><td colspan="5" class="empty">暂无数据</td></tr>';
                return;
            }

            users.forEach(function(u, i) {
                var rank = i + 1;
                var val = metric === 'coins' ? (u.coins || 0) : (metric === 'draws' ? (u.total_draws || 0) : (u.collection_count || 0));
                var tr = document.createElement('tr');
                tr.className = rank <= 3 ? 'top-' + rank : '';
                var medal = rank <= 3 ? ['🥇','🥈','🥉'][rank-1] : rank;
                var rankClass = rank <= 3 ? 'rank-' + rank : 'rank-other';
                tr.innerHTML =
                    '<td><span class="rank ' + rankClass + '">' + medal + '</span></td>' +
                    '<td><code>' + esc(u.user_id) + '</code></td>' +
                    '<td><strong>' + val.toLocaleString() + '</strong></td>' +
                    '<td>' + (u.total_draws || 0) + '</td>' +
                    '<td>' + (u.collection_count || 0) + '</td>';
                tbody.appendChild(tr);
            });
        } catch(e) {
            tbody.innerHTML = '<tr><td colspan="5" class="error">加载失败: ' + esc(e.message) + '</td></tr>';
        }

        $('leaderboard-metric').onchange = loadLeaderboard;
    }

    // ========== Wishes ==========
    var allWishes = [];

    async function loadWishes() {
        var tbody = $('wishes-body');
        tbody.innerHTML = '<tr><td colspan="3" class="empty">加载中...</td></tr>';

        try {
            var resp = await apiGet("wishes");
            allWishes = resp.wishes || [];
            renderWishes();
        } catch(e) {
            tbody.innerHTML = '<tr><td colspan="3" class="error">加载失败: ' + esc(e.message) + '</td></tr>';
        }

        $('wish-search').oninput = renderWishes;
    }

    function renderWishes() {
        var q = $('wish-search').value.trim().toLowerCase();
        var list = allWishes.filter(function(w) {
            var text = (w.content || w || '').toLowerCase();
            var uid = (w.user_id || '').toLowerCase();
            return !q || text.includes(q) || uid.includes(q);
        });

        $('wish-count').textContent = '共 ' + list.length + ' 条';
        var tbody = $('wishes-body');

        if (!list.length) {
            tbody.innerHTML = '<tr><td colspan="3" class="empty">无匹配愿望</td></tr>';
            return;
        }

        tbody.innerHTML = '';
        list.forEach(function(w) {
            var tr = document.createElement('tr');
            var content = esc(w.content || w || '');
            var date = esc(w.date || '未知');
            var uid = esc(w.user_id || '匿名');
            tr.innerHTML =
                '<td><code>' + uid + '</code></td>' +
                '<td class="wish-text">「' + content + '」</td>' +
                '<td>' + date + '</td>';
            tbody.appendChild(tr);
        });
    }

    // ========== Collectibles ==========

    // ========== User Modal ==========
    async function openUserModal(uid) {
        var modal = $('user-modal');
        var body = $('modal-body');
        modal.classList.add('show');
        body.innerHTML = '<div class="empty">加载中...</div>';

        try {
            var data = await apiGet("user/" + encodeURIComponent(uid));
            var pity = Math.max(0, 90 - (data.total_draws || 0));

            var html = '<div class="modal-kpi">';
            html += '<div class="modal-kpi-item"><div class="modal-kpi-val">' + (data.coins || 0) + '</div><div class="modal-kpi-lab">金币</div></div>';
            html += '<div class="modal-kpi-item"><div class="modal-kpi-val">' + (data.total_draws || 0) + '</div><div class="modal-kpi-lab">总抽数</div></div>';
            html += '<div class="modal-kpi-item"><div class="modal-kpi-val">' + pity + '</div><div class="modal-kpi-lab">距保底</div></div>';
            html += '</div>';

            html += '<div class="modal-section"><h4>📊 今日状态</h4>';
            html += '<div class="tag-list">';
            html += '<span class="tag">今日抽卡: ' + (data.daily_draws || 0) + '/10</span>';
            html += '<span class="tag">今日投喂: ' + (data.feed_count || 0) + '/3</span>';
            html += '<span class="tag">许愿数: ' + (data.wishes ? data.wishes.length : 0) + '</span>';
            html += '<span class="tag">收藏品: ' + (data.collection ? data.collection.length : 0) + '</span>';
            html += '</div></div>';

            if (data.collection && data.collection.length) {
                html += '<div class="modal-section"><h4>💎 收藏品</h4><div class="tag-list">';
                data.collection.forEach(function(c) {
                    html += '<span class="tag">' + esc(c) + '</span>';
                });
                html += '</div></div>';
            }

            if (data.wishes && data.wishes.length) {
                html += '<div class="modal-section"><h4>🌳 愿望记录</h4>';
                data.wishes.slice().reverse().forEach(function(w) {
                    var content = typeof w === 'string' ? w : (w.content || '');
                    var date = typeof w === 'string' ? '' : (' <span style="color:#6b7280">(' + esc(w.date || '') + ')</span>');
                    html += '<div class="tag" style="display:block;margin-bottom:6px;">「' + esc(content) + '」' + date + '</div>';
                });
                html += '</div>';
            }

            body.innerHTML = html;
        } catch(e) {
            body.innerHTML = '<div class="error">加载失败: ' + esc(e.message) + '</div>';
        }
    }

    $('modal-close').addEventListener('click', function() {
        $('user-modal').classList.remove('show');
    });
    $('user-modal').addEventListener('click', function(e) {
        if (e.target === this) this.classList.remove('show');
    });

    // ========== Init ==========
    function init() {
        initTabs();
        loadDashboard();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();