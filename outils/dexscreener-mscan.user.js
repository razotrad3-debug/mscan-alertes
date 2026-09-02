// ==UserScript==
// @name         MSCAN — mes trendlines
// @namespace    mscan
// @version      1.1
// @description  Envoie a MSCAN les trendlines que tu traces sur DexScreener
// @match        https://dexscreener.com/*
// @match        https://www.dexscreener.com/*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// @connect      localhost
// @run-at       document-idle
// ==/UserScript==

/*
 * Ce script ne lit QUE les coordonnees de tes traces, sur les pages
 * dexscreener.com, et les envoie a MSCAN qui tourne sur ta machine.
 * Rien ne part ailleurs. Aucun compte, aucun mot de passe, aucun cookie.
 *
 * Le chart de DexScreener est une TradingView dans une iframe de meme
 * origine : ses traces sont donc lisibles depuis la page. On y prend, pour
 * chaque ligne, ses une ou deux ancres (date + valeur). C'est tout.
 */
(function () {
  "use strict";

  var PORTS = [];
  for (var i = 0; i < 20; i++) PORTS.push(8787 + i);

  var port = null; // port ou repond MSCAN, trouve une fois
  var derniere = ""; // derniere signature envoyee
  var stable = null; // signature vue au tour precedent
  var vuNonVide = {}; // paires ou l'on a deja vu des traces

  // ── transport ────────────────────────────────────────────────────
  // GM_xmlhttpRequest passe par l'extension : ni CORS ni blocage
  // "reseau prive" ne s'appliquent, contrairement a un fetch() de la page.
  function req(method, url, body) {
    return new Promise(function (res, rej) {
      GM_xmlhttpRequest({
        method: method,
        url: url,
        headers: { "Content-Type": "application/json" },
        data: body ? JSON.stringify(body) : undefined,
        timeout: 8000,
        onload: function (r) {
          try { res(JSON.parse(r.responseText)); } catch (e) { rej(e); }
        },
        onerror: rej,
        ontimeout: rej,
      });
    });
  }

  async function trouverPort() {
    if (port) return port;
    for (var k = 0; k < PORTS.length; k++) {
      try {
        var d = await req("GET", "http://127.0.0.1:" + PORTS[k] + "/api/trendlines");
        if (d && d.app === "mscan") { port = PORTS[k]; return port; }
      } catch (e) { /* port muet : au suivant */ }
    }
    return null;
  }

  // ── lecture du chart ─────────────────────────────────────────────
  function lireLignes() {
    var f = null, tous = document.querySelectorAll("iframe");
    for (var k = 0; k < tous.length; k++) {
      if (/tradingview/i.test(tous[k].id)) { f = tous[k]; break; }
    }
    if (!f || !f.contentWindow) return null;

    var m;
    try {
      m = f.contentWindow.chartWidgetCollection
           .activeChartWidget.value().model().m_model;
    } catch (e) { return null; } // chart pas encore pret

    var out = [];
    var srcs;
    try { srcs = m.orderedDataSources(); } catch (e) { return null; }
    for (var j = 0; j < srcs.length; j++) {
      var s = srcs[j], pts;
      try {
        if (typeof s.points !== "function") continue;
        pts = s.points();
      } catch (e) { continue; }
      // au reveil, une ligne existe deja mais ses ancres sont vides pendant
      // une seconde : on l'ignore plutot que de la prendre pour un effacement
      if (!pts || !pts.length) continue;
      var ancres = [];
      for (var q = 0; q < pts.length && q < 2; q++) {
        ancres.push({ time: pts[q].time, price: pts[q].price });
      }
      out.push({ tool: s.toolname || "", points: ancres });
    }
    return out;
  }

  // ── petit retour visuel ──────────────────────────────────────────
  function dire(texte, ok) {
    var id = "mscan-toast", el = document.getElementById(id);
    if (!el) {
      el = document.createElement("div");
      el.id = id;
      el.style.cssText =
        "position:fixed;right:14px;bottom:14px;z-index:2147483647;" +
        "padding:9px 13px;border-radius:3px;font:12px/1.4 -apple-system," +
        "Segoe UI,sans-serif;letter-spacing:.2px;color:#fff;" +
        "background:#0b0c0e;border:1px solid rgba(255,255,255,.09);" +
        "box-shadow:0 6px 24px rgba(0,0,0,.5);transition:opacity .25s";
      document.body.appendChild(el);
    }
    el.textContent = texte;
    el.style.borderLeft = "2px solid " + (ok ? "#d4af37" : "#ff7a7a");
    el.style.opacity = "1";
    clearTimeout(el._t);
    el._t = setTimeout(function () { el.style.opacity = "0"; }, 4000);
  }

  // ── un tour ──────────────────────────────────────────────────────
  async function tour() {
    var mm = location.pathname.match(/^\/([a-z0-9-]+)\/([A-Za-z0-9]{20,})/);
    if (!mm) return;
    var chain = mm[1], pair = mm[2];

    var lignes = lireLignes();
    if (lignes === null) return; // chart pas pret

    if (lignes.length) vuNonVide[pair] = true;
    // ne jamais effacer ce que MSCAN garde tant qu'on n'a pas vu de traces
    // sur cette paire : au chargement, le chart est vide une seconde
    else if (!vuNonVide[pair]) return;

    var sig = chain + "/" + pair + "|" + JSON.stringify(lignes);
    if (sig === derniere) return;
    // on attend que le trace ne bouge plus : sans ca, une ligne en cours de
    // deplacement partirait a chaque pixel
    if (sig !== stable) { stable = sig; return; }

    var p = await trouverPort();
    if (!p) { dire("MSCAN introuvable — l'app est-elle ouverte ?", false); return; }

    try {
      var r = await req("POST", "http://127.0.0.1:" + p + "/api/trendlines",
                        { chain: chain, pair: pair, lines: lignes });
      derniere = sig;
      if (r && r.ok) {
        dire("MSCAN · " + r.recues + " ligne" + (r.recues > 1 ? "s" : "") +
             " sur " + r.symbol + (r.recues ? " — sous surveillance" : ""), true);
      } else {
        dire("MSCAN · " + ((r && r.erreur) || "refus"), false);
      }
    } catch (e) {
      port = null; // l'app a peut-etre ferme
      dire("MSCAN · envoi impossible", false);
    }
  }

  setInterval(function () { tour().catch(function () {}); }, 2000);
})();
