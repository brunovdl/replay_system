/*
 * Gestão dos replays guardados no servidor: listar, assistir, baixar e apagar.
 * Usa a mesma senha da filmagem (salva no navegador como replay_token).
 */
"use strict";

const $ = (id) => document.getElementById(id);
const estado = { token: "", arquivos: [], marcados: new Set(), urlVideo: null };

try { estado.token = localStorage.getItem("replay_token") || ""; } catch {}

function api(caminho, opcoes = {}) {
  const headers = { ...(opcoes.headers || {}), "X-Replay-Token": estado.token };
  return fetch(caminho, { ...opcoes, headers });
}

function mb(bytes) {
  return bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(2)} GB` : `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

function dataHora(segundos) {
  return new Date(segundos * 1000).toLocaleString("pt-BR", {
    weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

function mostrarErro(msg) { $("erro").textContent = msg || ""; }

async function carregar() {
  mostrarErro("");
  let resp;
  try {
    resp = await api("/api/arquivos");
  } catch {
    return mostrarErro("Sem conexão com o servidor.");
  }
  if (resp.status === 401) {
    $("painel").classList.add("hidden");
    $("login").classList.remove("hidden");
    if (estado.token) mostrarErro("Senha incorreta.");
    return;
  }
  if (!resp.ok) return mostrarErro(`Servidor respondeu ${resp.status}.`);

  const dados = await resp.json();
  $("login").classList.add("hidden");
  $("painel").classList.remove("hidden");
  estado.arquivos = dados.arquivos;
  // Remove da seleção o que não existe mais (apagado pela limpeza automática)
  const nomes = new Set(dados.arquivos.map((a) => a.nome));
  for (const n of estado.marcados) if (!nomes.has(n)) estado.marcados.delete(n);

  const limite = dados.max_storage_gb > 0 ? dados.max_storage_gb * 1024 ** 3 : 0;
  $("r-total").textContent = `${mb(dados.total)} em ${dados.arquivos.length} arquivo(s)` +
    (limite ? ` de ${mb(limite)}` : "");
  const pct = limite ? Math.min(100, (dados.total / limite) * 100) : 0;
  $("uso-nivel").style.width = `${pct}%`;
  $("uso-nivel").style.background = pct > 85 ? "var(--accent)" : pct > 60 ? "var(--warn)" : "var(--ok)";
  const regras = [];
  if (dados.keep_days > 0) regras.push(`apagados sozinhos após ${dados.keep_days} dia(s)`);
  if (limite) regras.push(`acima de ${mb(limite)} os mais antigos saem primeiro`);
  regras.push(`${mb(dados.disco_livre)} livres no servidor`);
  $("r-regras").textContent = "Replays " + regras.join(" · ");

  desenharLista();
}

function desenharLista() {
  const lista = $("lista");
  lista.textContent = "";
  $("vazio").classList.toggle("hidden", estado.arquivos.length > 0);

  for (const a of estado.arquivos) {
    const item = document.createElement("div");
    item.className = "item" + (estado.marcados.has(a.nome) ? " marcado" : "");

    const caixa = document.createElement("input");
    caixa.type = "checkbox";
    caixa.checked = estado.marcados.has(a.nome);
    caixa.addEventListener("change", () => {
      caixa.checked ? estado.marcados.add(a.nome) : estado.marcados.delete(a.nome);
      item.classList.toggle("marcado", caixa.checked);
      atualizarSelecao();
    });

    const info = document.createElement("div");
    info.className = "info";
    const data = document.createElement("div");
    data.className = "data";
    data.textContent = dataHora(a.criado);
    const detalhe = document.createElement("small");
    detalhe.textContent = `${mb(a.tamanho)} · ${a.nome}`;
    info.append(data, detalhe);
    if (a.tipo === "falha") {
      const aviso = document.createElement("small");
      aviso.className = "falha";
      aviso.textContent = "⚠ clipe que falhou na conversão (só para diagnóstico)";
      info.append(aviso);
    }

    const botoes = document.createElement("div");
    botoes.className = "botoes";
    if (a.tipo === "replay") botoes.append(botao("▶", "Assistir", () => assistir(a.nome)));
    botoes.append(botao("⬇", "Baixar", () => baixar(a.nome)));
    botoes.append(botao("🗑", "Apagar", () => apagar([a.nome])));

    item.append(caixa, info, botoes);
    lista.append(item);
  }
  atualizarSelecao();
}

function botao(texto, titulo, aoClicar) {
  const b = document.createElement("button");
  b.textContent = texto;
  b.title = titulo;
  b.setAttribute("aria-label", titulo);
  b.addEventListener("click", aoClicar);
  return b;
}

function atualizarSelecao() {
  const sel = estado.arquivos.filter((a) => estado.marcados.has(a.nome));
  $("barra-selecao").classList.toggle("hidden", sel.length === 0);
  $("n-sel").textContent = sel.length;
  $("t-sel").textContent = mb(sel.reduce((s, a) => s + a.tamanho, 0));
  const todos = sel.length === estado.arquivos.length && sel.length > 0;
  $("btn-marcar").textContent = todos ? "Desmarcar todos" : "Selecionar todos";
  $("btn-todos").disabled = estado.arquivos.length === 0;
}

/** Baixa o arquivo com a senha no header (um <a href> direto não teria como mandá-la). */
async function buscarBlob(nome) {
  const resp = await api(`/api/arquivos/${encodeURIComponent(nome)}`);
  if (!resp.ok) throw new Error(resp.status === 404 ? "O arquivo não existe mais." : `Erro ${resp.status}`);
  return resp.blob();
}

async function assistir(nome) {
  try {
    const blob = await buscarBlob(nome);
    fecharPlayer();
    estado.urlVideo = URL.createObjectURL(blob);
    $("video").src = estado.urlVideo;
    $("player").classList.remove("hidden");
    $("video").play().catch(() => {});
  } catch (e) {
    mostrarErro(e.message);
    carregar();
  }
}

function fecharPlayer() {
  $("video").pause();
  $("video").removeAttribute("src");
  $("video").load();
  if (estado.urlVideo) URL.revokeObjectURL(estado.urlVideo);
  estado.urlVideo = null;
  $("player").classList.add("hidden");
}

async function baixar(nome) {
  try {
    const url = URL.createObjectURL(await buscarBlob(nome));
    const a = document.createElement("a");
    a.href = url;
    a.download = nome;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  } catch (e) {
    mostrarErro(e.message);
  }
}

async function apagar(nomes, todos = false) {
  const texto = todos
    ? `Apagar TODOS os ${estado.arquivos.length} arquivos do servidor? Não dá para desfazer.`
    : nomes.length === 1
      ? "Apagar este replay do servidor? Não dá para desfazer."
      : `Apagar ${nomes.length} replays do servidor? Não dá para desfazer.`;
  if (!confirm(texto)) return;
  try {
    const resp = await api("/api/arquivos/apagar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(todos ? { todos: true } : { nomes }),
    });
    if (!resp.ok) throw new Error(`Erro ${resp.status} ao apagar.`);
    for (const n of nomes) estado.marcados.delete(n);
    if (todos) estado.marcados.clear();
  } catch (e) {
    mostrarErro(e.message);
  }
  carregar();
}

function entrar() {
  estado.token = $("senha").value.trim();
  try { localStorage.setItem("replay_token", estado.token); } catch {}
  carregar();
}

$("btn-entrar").addEventListener("click", entrar);
$("senha").addEventListener("keydown", (e) => e.key === "Enter" && entrar());
$("btn-atualizar").addEventListener("click", carregar);
$("btn-marcar").addEventListener("click", () => {
  const todos = estado.marcados.size === estado.arquivos.length;
  estado.marcados = new Set(todos ? [] : estado.arquivos.map((a) => a.nome));
  desenharLista();
});
$("btn-falhas").addEventListener("click", () => {
  estado.marcados = new Set(estado.arquivos.filter((a) => a.tipo === "falha").map((a) => a.nome));
  desenharLista();
});
$("btn-todos").addEventListener("click", () => apagar([], true));
$("btn-apagar-sel").addEventListener("click", () => apagar([...estado.marcados]));
$("btn-fechar").addEventListener("click", fecharPlayer);

carregar();
