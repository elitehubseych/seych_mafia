(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };

  var els = {
    roomCode: $("roomCode"),
    stateBadge: $("stateBadge"),
    nightLabel: $("nightLabel"),
    table: $("table"),
    timeline: $("timeline"),
    timelineInner: $("timelineInner"),
    selfCard: $("selfCard"),
    actionArea: $("actionArea"),
    chatWrap: $("chatWrap"),
    chatHint: $("chatHint"),
    chatInput: $("chatInput"),
    chatSend: $("chatSend"),
    roleCard: $("roleCard"),
    enterScreen: $("enterScreen"),
    codeInput: $("codeInput"),
    codeGo: $("codeGo"),
    enterError: $("enterError"),
    toast: $("toast"),
  };

  var state = null;
  var params = {};
  var roomId = null;
  var ws = null;
  var wsTimer = null;
  var pollTimer = null;
  var lastTimelineId = 0;
  var selection = null;

  var ROLE_DESC = {
    don: "Ты Дон — Глава мафии! Вместе с семьёй ночью выбираешь жертву и исполняешь приговор.",
    mafia: "Ты Мафия — член мафиозной семьи. Ночью вместе с семьёй выбираешь жертву.",
    citizen: "Ты Мирный житель! Днём ищи мафию, спорь и голосуй на собрании.",
    commissar: "Ты Коммисар! Ночью можешь проверить роль любого игрока или сделать один выстрел.",
    sergeant: "Ты Сержант — помощник коммисара. Узнаёшь обо всех его проверках, а при его гибели занимаешь его место.",
    doctor: "Ты Доктор! Ночью можешь спасти любого игрока. Один раз за игру — самого себя.",
    mistress: "Ты Любовница! Ночью нейтрализуешь игрока: с доном — мафия никого не убьёт, с коммисаром — он не проверит и не выстрелит.",
    lawyer: "Ты Адвокат! Ночью выбираешь подзащитного — коммисар увидит его мирным жителем.",
    kamikaze: "Ты Камикадзе! Когда тебя убивают ночью, ты взрываешься и забираешь убийцу с собой.",
    maniac: "Ты Маньяк! Ночью убиваешь. Останься единственным выжившим — и ты победишь.",
  };

  // ---------------------------------------------------------------- bootstrap
  function getLaunchParams() {
    var p = {};
    if (window.vkBridge) {
      try {
        window.vkBridge.send("VKWebAppInit").then(function () {
          return window.vkBridge.send("VKWebAppGetLaunchParams");
        }).then(function (lp) {
          Object.assign(p, lp.launch_params || lp || {});
        }).catch(function () {});
      } catch (e) { /* bridge not ready */ }
    }
    try {
      var hash = location.hash.replace(/^#/, "");
      if (hash) new URLSearchParams(hash).forEach(function (v, k) { if (!(k in p)) p[k] = v; });
    } catch (e) { /* ignore */ }
    try {
      new URLSearchParams(location.search).forEach(function (v, k) { if (!(k in p)) p[k] = v; });
    } catch (e) { /* ignore */ }
    return p;
  }

  function init() {
    params = getLaunchParams();
    els.codeGo.addEventListener("click", submitCode);
    els.codeInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter") submitCode();
    });
    els.chatSend.addEventListener("click", sendChat);
    els.chatInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter") sendChat();
    });
    els.selfCard.addEventListener("click", onSelfClick);

    var fromUrl = (params.room_id || "").trim().toUpperCase();
    if (fromUrl) {
      enterRoom(fromUrl);
    } else {
      els.enterScreen.classList.remove("hidden");
    }

    setTimeout(function () {
      var stored = localStorage.getItem("mafia_last_room");
      if (stored && !roomId) {
        els.codeInput.value = stored;
      }
    }, 50);
  }

  function submitCode() {
    var code = els.codeInput.value.trim().toUpperCase();
    if (!code) return;
    enterRoom(code);
  }

  function enterRoom(code) {
    roomId = code;
    els.enterError.textContent = "";
    els.enterScreen.classList.add("hidden");
    els.roomCode.textContent = roomId;
    try { localStorage.setItem("mafia_last_room", roomId); } catch (e) {}
    connectWS();
    fetchState();
  }

  // ---------------------------------------------------------------- api
  function buildQuery(extra) {
    var sp = new URLSearchParams();
    sp.set("room_id", roomId || "");
    for (var k in params) {
      if (Object.prototype.hasOwnProperty.call(params, k)) sp.set(k, params[k]);
    }
    if (extra) {
      for (var k2 in extra) {
        if (Object.prototype.hasOwnProperty.call(extra, k2)) sp.set(k2, extra[k2]);
      }
    }
    return sp;
  }

  function apiBase() {
    var m = location.search.match(/[?&]api=([^&]+)/);
    if (m) return decodeURIComponent(m[1]).replace(/\/+$/, "");
    return location.origin;
  }

  function apiUrl(path, query) {
    var u = apiBase() + path;
    if (query) u += "?" + query.toString();
    return u;
  }

  function apiWsUrl(query) {
    var base = apiBase().replace(/^https:/, "ws").replace(/^http:/, "ws");
    return base + "/app/ws?" + query.toString();
  }

  function fetchState() {
    if (!roomId) return Promise.resolve();
    return fetch(apiUrl("/app/state", buildQuery()))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data && data.room_id) {
          applyState(data);
        } else if (data && data.error) {
          showError(data.error);
        }
      })
      .catch(function (e) { console.warn("state fetch failed", e); });
  }

  function postAction(action, payload) {
    if (!roomId) return;
    fetch(apiUrl("/app/action"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ room_id: roomId, action: action, payload: payload || {}, params: params }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data && data.msg) toast(data.msg, !!data.ok);
        if (data && data.ok) {
          selection = null;
          fetchState();
        } else if (data && data.error) {
          showError(data.error);
        }
      })
      .catch(function () { toast("Сеть недоступна", false); });
  }

  function connectWS() {
    clearTimeout(wsTimer);
    if (!roomId) return;
    var url = apiWsUrl(buildQuery());
    try { ws = new WebSocket(url); } catch (e) { scheduleReconnect(); return; }
    ws.onmessage = function (ev) {
      var data;
      try { data = JSON.parse(ev.data); } catch (e) { return; }
      if (data && data.room_id) applyState(data);
    };
    ws.onclose = function () { scheduleReconnect(); };
    ws.onerror = function () { try { ws.close(); } catch (e) {} };
    clearInterval(pollTimer);
    pollTimer = setInterval(fetchState, 25000);
  }

  function scheduleReconnect() {
    clearTimeout(wsTimer);
    wsTimer = setTimeout(connectWS, 3000);
  }

  // ---------------------------------------------------------------- state
  function applyState(s) {
    var prev = state;
    state = s;
    renderHeader();
    renderSeats();
    renderTimeline();
    renderSelfCard();
    renderActionArea();
    renderChat();
    maybeRoleCard(prev);
  }

  function renderHeader() {
    var st = state.state;
    var label = "—";
    var cls = "";
    if (st === "waiting") { label = "Регистрация"; cls = "waiting"; }
    else if (st === "night") { label = "Ночь"; cls = "night"; }
    else if (st === "voting" || st === "confirm") { label = "День"; cls = "voting"; }
    else if (st === "ended") { label = "Окончена"; cls = "ended"; }
    els.stateBadge.textContent = label;
    els.stateBadge.className = "badge " + cls;
    els.nightLabel.textContent = state.night_number > 0 ? "Ночь " + state.night_number : "";
  }

  function seatPos(i, n) {
    var per = 4;
    var d = ((i + 0.5) / n) * per;
    var x, y;
    if (d < 1) { x = d; y = 0; }
    else if (d < 2) { x = 1; y = d - 1; }
    else if (d < 3) { x = 3 - d; y = 1; }
    else { x = 0; y = 4 - d; }
    var inset = 0.10;
    var span = 1 - 2 * inset;
    return { x: inset + x * span, y: inset + y * span };
  }

  function avatarNode(p) {
    if (p.avatar) {
      var img = document.createElement("img");
      img.className = "avatar";
      img.src = p.avatar;
      img.alt = "";
      img.onerror = function () { img.style.display = "none"; };
      return img;
    }
    var fb = document.createElement("div");
    fb.className = "avatar-fallback";
    fb.textContent = (p.name || "?").charAt(0).toUpperCase();
    return fb;
  }

  function renderSeats() {
    var old = els.table.querySelectorAll(".seat");
    for (var i = 0; i < old.length; i++) old[i].remove();

    var n = state.players.length;
    var selectable = {};
    if (state.you.awaiting === "target") {
      state.you.targets.forEach(function (u) { selectable[u] = true; });
    }
    if (state.you.awaiting === "vote") {
      state.you.vote_targets.forEach(function (u) { selectable[u] = true; });
    }

    state.players.forEach(function (p, idx) {
      var pos = seatPos(idx, n);
      var seat = document.createElement("div");
      seat.className = "seat" + (p.alive ? "" : " dead-slot") + (p.uid === state.me ? " me" : "");
      if (selectable[p.uid]) seat.classList.add("selectable");
      if (selection && selection.uid === p.uid && selectable[p.uid]) seat.classList.add("selected");
      seat.style.left = (pos.x * 100) + "%";
      seat.style.top = (pos.y * 100) + "%";
      seat.dataset.uid = p.uid;

      var wrap = document.createElement("div");
      wrap.className = "avatar-wrap";
      wrap.appendChild(avatarNode(p));

      var num = document.createElement("div");
      num.className = "num";
      num.textContent = p.num;
      wrap.appendChild(num);

      if (!p.alive) {
        var dead = document.createElement("div");
        dead.className = "dead";
        dead.textContent = "💀";
        wrap.appendChild(dead);
      }
      if (p.banned) {
        var banned = document.createElement("div");
        banned.className = "banned-tag";
        banned.textContent = "🔨";
        wrap.appendChild(banned);
      }
      seat.appendChild(wrap);

      var name = document.createElement("div");
      name.className = "name";
      name.textContent = p.name;
      seat.appendChild(name);

      if (p.role) {
        var tag = document.createElement("div");
        tag.className = "role-tag";
        tag.textContent = p.role_emoji + " " + p.role_ru;
        seat.appendChild(tag);
      }

      seat.addEventListener("click", function () {
        if (state.you.awaiting === "target" && selectable[p.uid]) {
          selection = { uid: p.uid };
          highlightPicks(p.uid);
          renderSeats();
        } else if (state.you.awaiting === "vote" && selectable[p.uid]) {
          selection = { uid: p.uid };
          highlightPicks(p.uid);
          renderSeats();
        }
      });

      els.table.appendChild(seat);
    });
  }

  function highlightPicks(uid) {
    var picks = document.querySelectorAll(".pick");
    for (var i = 0; i < picks.length; i++) {
      picks[i].classList.toggle("selected", Number(picks[i].dataset.uid) === uid);
    }
  }

  function renderTimeline() {
    var items = state.timeline || [];
    if (items.length === 0) {
      els.timelineInner.innerHTML = '<div class="tl-empty">Здесь будет игровая хроника</div>';
      lastTimelineId = 0;
      return;
    }
    if (lastTimelineId === 0 && els.timelineInner.firstChild) {
      els.timelineInner.innerHTML = "";
    }
    items.forEach(function (ev) {
      if (ev.id <= lastTimelineId) return;
      lastTimelineId = ev.id;
      var div = document.createElement("div");
      div.className = "tl-item" + (ev.vis === "user:" + state.me ? " me-msg" : "");
      div.textContent = ev.text;
      els.timelineInner.appendChild(div);
    });
    els.timeline.scrollTop = els.timeline.scrollHeight;
  }

  function renderSelfCard() {
    var me = null;
    for (var i = 0; i < state.players.length; i++) {
      if (state.players[i].uid === state.me) { me = state.players[i]; break; }
    }
    els.selfCard.innerHTML = "";
    if (!me) return;

    var av = document.createElement("div");
    av.className = "sc-avatar";
    av.appendChild(avatarNode(me));
    els.selfCard.appendChild(av);

    var nm = document.createElement("div");
    nm.className = "sc-name";
    nm.textContent = me.name;
    els.selfCard.appendChild(nm);

    if (me.role) {
      var r = document.createElement("div");
      r.className = "sc-role";
      r.textContent = me.role_emoji + " " + me.role_ru;
      els.selfCard.appendChild(r);
    } else if (state.is_admin) {
      var h = document.createElement("div");
      h.className = "sc-hint";
      h.textContent = "Создатель комнаты";
      els.selfCard.appendChild(h);
    }
  }

  function onSelfClick() {
    var me = null;
    for (var i = 0; i < state.players.length; i++) {
      if (state.players[i].uid === state.me) { me = state.players[i]; break; }
    }
    if (me && me.role) showRoleCard(me);
  }

  // ---------------------------------------------------------------- actions
  function btn(label, cls, fn) {
    var b = document.createElement("button");
    b.className = "btn" + (cls ? " " + cls : "");
    b.textContent = label;
    b.addEventListener("click", fn);
    return b;
  }

  function pickTitle(kind) {
    var y = state.you;
    if (kind === "vote") return "За кого голосуем?";
    var role = y.role;
    if (role === "doctor") return "🩺 Кого лечим?";
    if (role === "mistress") return "💋 С кем проведём ночь?";
    if (role === "lawyer") return "⚖️ Кого защитим?";
    if (role === "maniac") return "🔪 Кого убиваем?";
    if (role === "kamikaze") return "💥 Кого заберём с собой?";
    if (role === "commissar") return "🕵️ Кого проверяем?";
    if (role === "don" || role === "mafia") return "🗳️ Кого приводим в жертву?";
    return "Выбери игрока";
  }

  function renderPickInline(kind) {
    var y = state.you;
    var pool = kind === "target"
      ? state.players.filter(function (p) { return y.targets.indexOf(p.uid) >= 0; })
      : state.players.filter(function (p) { return y.vote_targets.indexOf(p.uid) >= 0; });

    var title = document.createElement("div");
    title.className = "chat-hint";
    title.textContent = pickTitle(kind);
    els.actionArea.appendChild(title);

    var scroller = document.createElement("div");
    scroller.className = "pick-scroll";
    var grid = document.createElement("div");
    grid.className = "pick-grid";
    pool.forEach(function (p) {
      var pick = document.createElement("div");
      pick.className = "pick" + (p.alive ? "" : " dead");
      pick.dataset.uid = p.uid;
      if (selection && selection.uid === p.uid) pick.classList.add("selected");

      var av = document.createElement("div");
      av.className = "pk-avatar";
      av.appendChild(avatarNode(p));
      pick.appendChild(av);

      var nm = document.createElement("div");
      nm.className = "pk-name";
      nm.textContent = p.num + ". " + p.name;
      pick.appendChild(nm);

      pick.addEventListener("click", function () {
        selection = { uid: p.uid };
        highlightPicks(p.uid);
        renderSeats();
      });
      grid.appendChild(pick);
    });
    scroller.appendChild(grid);
    els.actionArea.appendChild(scroller);

    var row = document.createElement("div");
    row.className = "btn-row";
    var skipLabel = kind === "vote" ? "🤐 Воздержаться" : "⏭️ Пропустить";
    row.appendChild(btn(skipLabel, "btn-secondary", function () {
      postAction(kind === "vote" ? "abstain" : "skip", {});
    }));
    row.appendChild(btn("✅ Подтвердить", "btn-green", function () {
      if (selection) {
        postAction(kind === "vote" ? "vote" : "target", { uid: selection.uid });
      } else {
        toast("Выбери игрока", false);
      }
    }));
    els.actionArea.appendChild(row);
  }

  function renderActionArea() {
    els.actionArea.innerHTML = "";
    var y = state.you;
    var st = state.state;

    if (st === "waiting") {
      if (!y.in_game) {
        els.actionArea.appendChild(btn("➕ Присоединиться", "btn-green btn-block", function () {
          postAction("join", {});
        }));
      } else {
        var info = document.createElement("div");
        info.className = "chat-hint";
        info.textContent = "Ожидание начала… (мин. 4 игрока)";
        els.actionArea.appendChild(info);
        if (state.is_admin && state.players.length >= 4) {
          els.actionArea.appendChild(btn("🚀 Начать игру", "btn-gold btn-block", function () {
            postAction("start", {});
          }));
        }
      }
      return;
    }

    if (st === "ended") {
      var end = document.createElement("div");
      end.className = "chat-hint";
      end.textContent = state.ended_title || "Игра окончена";
      els.actionArea.appendChild(end);
      return;
    }

    if (st === "night" || st === "voting") {
      if (y.awaiting === "mode") {
        var grid = document.createElement("div");
        grid.className = "actions-grid";
        grid.appendChild(btn("🕵️ Проверить", null, function () { postAction("mode", { mode: "check" }); }));
        grid.appendChild(btn("🔫 Стрелять", null, function () { postAction("mode", { mode: "shoot" }); }));
        grid.appendChild(btn("😴 Пропустить", "btn-secondary", function () { postAction("skip", {}); }));
        els.actionArea.appendChild(grid);
      } else if (y.awaiting === "target") {
        renderPickInline("target");
      } else if (y.awaiting === "vote") {
        renderPickInline("vote");
      }
      if (state.is_admin) {
        els.actionArea.appendChild(btn("⏹️ Остановить", "btn-danger", function () {
          postAction("stop", {});
        }));
      }
      return;
    }

    if (st === "confirm") {
      if (y.awaiting === "confirm") {
        var row = document.createElement("div");
        row.className = "btn-row";
        row.appendChild(btn("👍 Повесить", "btn-green", function () { postAction("confirm", { v: "like" }); }));
        row.appendChild(btn("👎 Помиловать", "btn-danger", function () { postAction("confirm", { v: "dislike" }); }));
        els.actionArea.appendChild(row);
      } else {
        var w = document.createElement("div");
        w.className = "chat-hint";
        if (state.confirm) {
          w.textContent = "Казнь " + (state.confirm.target_name || "?") + ": 👍 " + state.confirm.likes + " | 👎 " + state.confirm.dislikes;
        }
        els.actionArea.appendChild(w);
      }
      if (state.is_admin) {
        els.actionArea.appendChild(btn("⏹️ Остановить", "btn-danger", function () {
          postAction("stop", {});
        }));
      }
      return;
    }
  }

  function renderChat() {
    var y = state.you;
    if (y.last_words_open) {
      els.chatWrap.classList.remove("hidden");
      els.chatHint.textContent = "✍️ Напиши последние слова — город их услышит";
      els.chatInput.placeholder = "Последние слова…";
    } else if (y.can_chat) {
      els.chatWrap.classList.remove("hidden");
      els.chatHint.textContent = y.chat_hint || "";
      els.chatInput.placeholder = "Сообщение…";
    } else {
      els.chatWrap.classList.add("hidden");
    }
  }

  function sendChat() {
    if (!state) return;
    var text = els.chatInput.value.trim();
    if (!text) return;
    var action = state.you.last_words_open ? "lastwords" : "chat";
    postAction(action, { text: text });
    els.chatInput.value = "";
  }

  // ---------------------------------------------------------------- role card
  function maybeRoleCard(prev) {
    var me = null;
    for (var i = 0; i < state.players.length; i++) {
      if (state.players[i].uid === state.me) { me = state.players[i]; break; }
    }
    if (!me || !me.role) return;
    var prevMe = null;
    if (prev) {
      for (var j = 0; j < prev.players.length; j++) {
        if (prev.players[j].uid === state.me) { prevMe = prev.players[j]; break; }
      }
    }
    if (!prevMe || prevMe.role !== me.role) {
      showRoleCard(me);
    }
  }

  function showRoleCard(me) {
    var cls = "role-card";
    if (me.role === "don" || me.role === "mafia") cls += " mafia";
    else if (me.role === "maniac") cls += " maniac";
    els.roleCard.className = cls;
    els.roleCard.innerHTML = "";

    var e = document.createElement("div");
    e.className = "rc-emoji";
    e.textContent = me.role_emoji;
    els.roleCard.appendChild(e);

    var r = document.createElement("div");
    r.className = "rc-role";
    r.textContent = "Ты — " + me.role_ru;
    els.roleCard.appendChild(r);

    var d = document.createElement("div");
    d.className = "rc-desc";
    d.textContent = ROLE_DESC[me.role] || "";
    els.roleCard.appendChild(d);

    var c = document.createElement("button");
    c.className = "btn btn-block btn-secondary rc-close";
    c.textContent = "Понятно";
    c.addEventListener("click", function () { els.roleCard.classList.add("hidden"); });
    els.roleCard.appendChild(c);

    els.roleCard.classList.remove("hidden");
  }

  // ---------------------------------------------------------------- misc
  function toast(msg, ok) {
    els.toast.textContent = msg;
    els.toast.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { els.toast.classList.add("hidden"); }, 3000);
  }

  function showError(msg) {
    if (!roomId || msg === "room not found") {
      els.enterError.textContent = "Комната не найдена. Проверь код.";
      els.enterScreen.classList.remove("hidden");
    } else {
      toast(msg, false);
    }
  }

  init();
})();
