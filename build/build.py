#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gera a dashboard estatica (index.html) a partir da aba "Pagina 1" (Meta Ads) da
planilha da Clinica PRC.

Este cliente so tem uma fonte de dados: o gerenciador de anuncios (Meta Ads),
com investimento/impressoes/cliques por dia/campanha/conjunto/anuncio e a
coluna nativa "Messaging Conversations Started" (conversas de WhatsApp
iniciadas), usada como proxy de Lead. Nao ha planilha de Conversas, Leads
(popup/form) nem Compradores para cruzar por telefone — por isso:

  - leads[] e SINTETIZADO a partir de "Messaging Conversations Started": para
    cada linha do Meta Ads, geramos N leads (N = a coluna arredondada) com
    camp/adset/ad/data daquela linha e q=1 (nao ha segunda camada de
    qualificacao nesta conta, entao Leads == MQLs == 100% nesta dashboard).
  - sales[] fica sempre vazio: Vendas/Faturamento/CAC/ROAS aparecem como "-"
    em toda a dashboard ate existir uma fonte de compradores.
  - ad_links fica vazio: nao ha coluna de permalink de criativo nesta planilha.
  - seg[] (Seguidores) vem de OUTRA planilha (Controle de trafego, aba do mes
    atual, bloco "META — Seguidores") — ver load_seguidores().
  - lp{} (pagina "Funil landing page") vem da planilha de leads da LP (com
    UTMs). As campanhas cujo nome aparece no utm_campaign desses leads saem de
    meta[]/leads[] e vao para lp.meta — ver load_lp_leads()/build_lp().

Este script apenas LE a planilha (export CSV publico) e emite os REGISTROS
BRUTOS (leads[], meta[]) dentro do HTML. Todos os filtros, agregacoes, KPIs,
tabelas e graficos sao calculados no navegador (client-side). Nunca escreve
nada de volta.

Teste local: --meta-file / --seg-file / --lp-file apontando para CSVs baixados.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

SPREADSHEET_ID = "1SrzEB16RhXoQm28tNRF4TcUmhYqaTJZBlq5xQCCRkLE"
GID_META = "0"
EXPORT_URL = "https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={gid}"

# Seguidores: planilha "Clínica PRC | Controle de tráfego - 2026", uma aba por mês
# ("📈 Abr", "📈 Mai", ... "📈 Set"), bloco "META — Seguidores" (Invest. · Seguid. · CPS),
# uma linha por dia. Só a aba do MÊS ATUAL é lida.
SEG_SPREADSHEET_ID = "1BZBBwaAN1wBy6bzDxeEN51CkMJ82He-ckhhOzYifrpY"
MESES_PT = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
# gid já conhecido de cada aba mensal (abreviação do mês -> gid) — tentado
# primeiro; se o mês não estiver aqui, o gid é descoberto no htmlview.
SEG_GIDS_CONHECIDOS = {"Set": "1349189258"}
HTMLVIEW_URL = "https://docs.google.com/spreadsheets/d/{sid}/htmlview"

# Funil landing page: planilha "Planilha - Novo funil de agendamentos", aba
# "Leads" (1 linha por lead do formulário da LP, com UTMs). A aba é achada pelo
# CABEÇALHO (Data/hora + utm_campaign), não pelo gid — tenta os gids abaixo e
# depois os do htmlview. Nome/WhatsApp/ID do lead NUNCA entram no HTML (a
# dashboard é pública); a Queixa só entra agregada por dia (lp_queixas).
LP_SPREADSHEET_ID = "1gc4pYfTn1SLrFQZBYdNjHgYq_biaFmOpYJ7QWidYuQw"
LP_GIDS_CONHECIDOS = ["0"]

# Identificação do cliente/conta (usada só em textos/relatórios — não afeta o cruzamento de dados).
CLIENT_NAME = "Clínica PRC"
MAIN_PRODUCT = "PRC"
# Prefixo comum a TODAS as campanhas da conta (informativo — não filtra campanhas).
MAIN_PRODUCT_PREFIX = "PRC | E2-CAP"

BRT = timezone(timedelta(hours=-3))   # horario de Brasilia (exibicao)
TAX_FACTOR = 1.1385   # imposto/taxa da conta de mídia (13,85%)

# --------------------------------------------------------------------------- #
# Regras da aba Relatório (Top/Piores anúncios)
# --------------------------------------------------------------------------- #
# Amostra mínima para julgar um anúncio como "vencedor" ou "ruim". Abaixo disso
# ele entra como "Em observação" (dado insuficiente) — nunca é classificado só
# porque teve 1 resultado com pouco investimento. Ajuste conforme o ticket/CAC.
SAMPLE_MIN_SPEND = 100.0   # gasto mínimo (R$) para amostra relevante
SAMPLE_MIN_MQLS = 3        # leads mínimos para julgar qualidade profunda
TOP_ADS_N = 10             # nº de linhas em Top / Piores anúncios

# Metas & parâmetros da conta (DEFAULTS do painel editável da aba Relatório).
# São só o valor inicial: o usuário edita no navegador (persistido em
# localStorage) e as tabelas de anúncios recoram CPL e reavaliam a
# amostra ao vivo. None = "meta não definida" (métrica aparece sem cor até o
# gestor preencher).
META_CPMQL = None          # meta de CPL (R$/conversa); None = não definida
VOLUME_MIN_AMOSTRAL = SAMPLE_MIN_MQLS  # conversas mínimas p/ amostra confiável
N_DIAS_CORTE = 5           # dias consecutivos acima do teto p/ considerar corte


# --------------------------------------------------------------------------- #
# Leitura
# --------------------------------------------------------------------------- #
def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "dash-template-bot/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_csv(url: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(fetch_text(url))))


def read_csv_file(path: str) -> list[list[str]]:
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        return list(csv.reader(f))


def load_rows(url: str, local: str | None) -> list[list[str]]:
    return read_csv_file(local) if local else fetch_csv(url)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm(s: str | None) -> str:
    return strip_accents((s or "").strip().lower())


def to_float(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d,.\-]", "", str(v).strip())
    if not s:
        return 0.0
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_date(v: str) -> str | None:
    if not v:
        return None
    s = str(v).strip()
    if not s:
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%d/%m/%y", "%b %d, %Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- #
# Indexacao das colunas
# --------------------------------------------------------------------------- #
def header_index(header, wanted, fallback):
    idx = {}
    hn = [norm(h) for h in header]
    for key, aliases in wanted.items():
        found = None
        for a in aliases:
            a = norm(a)
            for i, h in enumerate(hn):
                if h == a or (a and a in h):
                    found = i
                    break
            if found is not None:
                break
        idx[key] = found if found is not None else fallback.get(key)
    return idx


def cell(row, i):
    if i is None or i < 0 or i >= len(row):
        return ""
    return (row[i] or "").strip()


# --------------------------------------------------------------------------- #
# Seguidores (aba mensal da planilha de Controle de tráfego)
# --------------------------------------------------------------------------- #
def parse_seguidores(rows):
    """Acha o bloco "META — Seguidores" na grade da aba e devolve [{d, sp, n}]
    (1 por dia: investimento e seguidores GANHOS no dia). None = bloco não
    encontrado (aba errada). Título da seção numa linha e cabeçalhos
    (Invest. · Seguid.) na mesma linha ou na de baixo; data na coluna "Data".
    O CPS da planilha não é lido: somar CPS diário daria errado num período —
    o front recalcula Invest ÷ Seguidores sobre os totais."""
    for r, row in enumerate(rows[:10]):
        for c, v in enumerate(row):
            nv = norm(v)
            if "meta" not in nv or "seguidores" not in nv:
                continue
            for hr in (r, r + 1):
                if hr >= len(rows):
                    continue
                h = rows[hr]
                if "invest" not in norm(cell(h, c)) or "seguid" not in norm(cell(h, c + 1)):
                    continue
                dcol = next((i for i, x in enumerate(h) if "data" in norm(x)), None)
                if dcol is None:
                    continue
                out = []
                for row2 in rows[hr + 1:]:
                    d = parse_date(cell(row2, dcol))
                    if d:
                        out.append({"d": d, "sp": round(to_float(cell(row2, c)), 2),
                                    "n": to_float(cell(row2, c + 1))})
                return out
    return None


def gids_da_planilha(sid: str) -> list[str]:
    """Todos os gids de aba que aparecem no htmlview público da planilha, em
    ordem e sem repetição. Não depende do nome da aba (o emoji "📈" pode vir
    escapado de várias formas) — a aba certa é escolhida pelo conteúdo."""
    html = fetch_text(HTMLVIEW_URL.format(sid=sid))
    achados = re.findall(r'(?:gid(?:=|\\x3d|\\u003d|%3D)|sheet-button-|gid"?\s*:\s*")(\d+)', html)
    return list(dict.fromkeys(achados))


def load_seguidores(local: str | None, hoje: datetime) -> list[dict]:
    """Lê a aba do mês atual pelo export CSV por gid (grade exata, como é
    exibida — o gviz foi descartado porque zera células ao inferir o tipo da
    coluna). A aba certa é a que tem o bloco "META — Seguidores" com datas do
    MÊS ATUAL — não depende do nome nem do título (a aba Set diz "Ago" no
    título). Tenta primeiro o gid de SEG_GIDS_CONHECIDOS; senão varre os gids
    do htmlview. A lista do htmlview é buscada em TODO build (1 requisição
    leve) e o log diz se o gid do mês aparece nela — assim cada build prova que
    a descoberta vai funcionar na virada do mês, sem esperar o dia 1º.
    Nunca derruba o build: sem aba do mês (ex.: dia 1º antes de criarem
    "📈 Out"), devolve [] e a dash mostra Seguidores como "-"."""
    if local:
        return parse_seguidores(read_csv_file(local)) or []
    abbr = MESES_PT[hoje.month - 1]
    mes = hoje.strftime("%Y-%m")
    sid = SEG_SPREADSHEET_ID

    def tenta(gid):
        try:
            seg = parse_seguidores(fetch_csv(EXPORT_URL.format(sid=sid, gid=gid)))
        except Exception as e:
            print(f"  seguidores: gid {gid} falhou ({e})", file=sys.stderr)
            return None
        if not seg:
            return None
        return seg

    try:
        gids = gids_da_planilha(sid)
    except Exception as e:
        print(f"  seguidores: htmlview falhou ({e})", file=sys.stderr)
        gids = []
    conhecido = SEG_GIDS_CONHECIDOS.get(abbr)
    print(f"  seguidores: {len(gids)} abas no htmlview"
          + (f" (gid do mês {conhecido} na lista: {'sim' if conhecido in gids else 'NÃO'})" if conhecido else ""),
          file=sys.stderr)
    # A planilha pode ter MAIS DE UMA aba do mês (ex.: "📈 Out" e uma cópia
    # " 📈 Out" com espaço, vazia). Avalia todas e fica com a que tem mais
    # dados preenchidos (dias com movimento, depois seguidores + investimento).
    def peso(seg):
        ativos = [s for s in seg if s["n"] or s["sp"]]
        return (len(ativos), sum(s["n"] for s in ativos), sum(s["sp"] for s in ativos))

    # Lê TODAS as abas mensais (histórico p/ a aba Seguidores). Pode haver
    # mais de uma aba do mesmo mês (cópia vazia) — fica a com mais dados.
    por_mes = {}
    for gid in dict.fromkeys(([conhecido] if conhecido else []) + gids):
        seg = tenta(gid)
        if seg is None:
            continue
        mes_aba = max({s["d"][:7] for s in seg}, key=lambda m: sum(s["d"].startswith(m) for s in seg))
        seg = [s for s in seg if s["d"].startswith(mes_aba)]
        print(f"  seguidores: candidata {mes_aba} gid {gid} — {peso(seg)[0]} dias com movimento", file=sys.stderr)
        if mes_aba not in por_mes or peso(seg) > peso(por_mes[mes_aba]):
            por_mes[mes_aba] = seg
    if mes not in por_mes:
        print(f"  AVISO seguidores: nenhuma aba com 'META — Seguidores' de {mes}", file=sys.stderr)
    return sorted((s for m in por_mes.values() for s in m), key=lambda s: s["d"])


# --------------------------------------------------------------------------- #
# Funil landing page (planilha de leads da LP, com UTMs)
# --------------------------------------------------------------------------- #
def nkey(v) -> str:
    """Chave de comparação de nomes (campanha/conjunto/anúncio x UTM): sem
    acento, minúscula, espaços colapsados."""
    return re.sub(r"\s+", " ", norm(v))


def utm_val(v: str) -> str:
    """Valor de UTM como gravado pela LP. Se veio cru da URL (ex.:
    "PRC%20%7C%20E2-CAP" ou "PRC+|+E2-CAP"), decodifica; senão mantém."""
    s = (v or "").strip()
    if "%" in s or ("+" in s and " " not in s):
        s = urllib.parse.unquote_plus(s).strip()
    return s


def parse_lp_datetime(v: str) -> str | None:
    """Data (BRT) do "Data/hora" da LP: aceita "16/10/2026 14:32:10",
    "16/10/2026" e ISO ("2026-10-16T17:32:10.000Z" — convertido para BRT)."""
    s = (v or "").strip()
    if not s:
        return None
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.match(r"\d{4}-\d{2}-\d{2}T[\d:.]+(Z|[+-]\d{2}:?\d{2})?$", s)
    if m and m.group(1):
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt.astimezone(BRT).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return parse_date(s)


def find_lp_header(rows):
    """Índice da linha de cabeçalho da aba de leads da LP (tem utm_campaign e
    uma coluna de data) — None se a aba não for a de leads."""
    for i, row in enumerate(rows[:5]):
        hn = [norm(x) for x in row]
        if "utm_campaign" in hn and any(h.startswith("data") for h in hn):
            return i
    return None


def parse_lp_rows(rows):
    """Linhas da aba de leads da LP -> registros crus (sem nome/WhatsApp/ID).
    None = não é a aba de leads."""
    hi = find_lp_header(rows)
    if hi is None:
        return None
    idx = header_index(
        rows[hi],
        {"dt": ["data/hora", "data"], "queixa": ["queixa"], "per": ["periodo"], "st": ["status"],
         "src": ["utm_source"], "med": ["utm_medium"], "camp": ["utm_campaign"], "content": ["utm_content"],
         "term": ["utm_term"], "disp": ["dispositivo"], "pg": ["pagina"]},
        {},
    )
    out = []
    for row in rows[hi + 1:]:
        if not any((c or "").strip() for c in row):
            continue
        d = parse_lp_datetime(cell(row, idx["dt"]))
        if not d:
            continue
        pg = cell(row, idx["pg"])
        pg = urllib.parse.urlsplit(pg).path if pg.startswith("http") else pg.split("?")[0]
        out.append({
            "d": d,
            "src": utm_val(cell(row, idx["src"])), "med": utm_val(cell(row, idx["med"])),
            "camp": utm_val(cell(row, idx["camp"])), "content": utm_val(cell(row, idx["content"])),
            "term": utm_val(cell(row, idx["term"])),
            "st": cell(row, idx["st"]), "per": cell(row, idx["per"]), "disp": cell(row, idx["disp"]),
            "pg": pg, "queixa": cell(row, idx["queixa"]),
        })
    return out


def load_lp_leads(local: str | None) -> list[dict]:
    """Leads da LP (aba achada pelo cabeçalho). Nunca derruba o build: sem
    acesso/aba, devolve [] e a página "Funil landing page" mostra zero leads."""
    if local:
        return parse_lp_rows(read_csv_file(local)) or []
    sid = LP_SPREADSHEET_ID
    gids = list(LP_GIDS_CONHECIDOS)
    try:
        gids += gids_da_planilha(sid)
    except Exception as e:
        print(f"  lp: htmlview falhou ({e})", file=sys.stderr)
    for gid in dict.fromkeys(gids):
        try:
            recs = parse_lp_rows(fetch_csv(EXPORT_URL.format(sid=sid, gid=gid)))
        except Exception as e:
            print(f"  lp: gid {gid} falhou ({e})", file=sys.stderr)
            continue
        if recs is not None:
            print(f"  lp: aba de leads = gid {gid} — {len(recs)} leads", file=sys.stderr)
            return recs
    print("  AVISO lp: aba de leads (Data/hora + utm_campaign) não encontrada — Funil LP sem leads",
          file=sys.stderr)
    return []


def lp_campaign_keys(lp_raw) -> set[str]:
    """Campanhas do funil LP = as que aparecem no utm_campaign dos leads."""
    return {nkey(r["camp"]) for r in lp_raw if r["camp"]}


def build_lp(lp_raw, lp_meta):
    """Liga os leads da LP aos nomes do Meta Ads: campanha = utm_campaign,
    conjunto = utm_term (ou utm_medium, se for esse que bate com um conjunto),
    anúncio = utm_content. Sem UTM -> "(sem UTM)" etc. Queixa sai SÓ agregada
    por dia (lp_queixas) — nenhum registro individual carrega a queixa."""
    names = {k: {} for k in ("camp", "adset", "ad")}
    for m in lp_meta:
        for k in names:
            names[k].setdefault(nkey(m[k]), m[k])

    def match(kind, *vals):
        for v in vals:
            if v and nkey(v) in names[kind]:
                return names[kind][nkey(v)]
        return None

    leads, queixas = [], {}
    for r in lp_raw:
        tem_utm = any(r[k] for k in ("src", "camp", "content", "term", "med"))
        leads.append({
            "d": r["d"],
            "camp": match("camp", r["camp"]) or r["camp"] or "(sem UTM)",
            "adset": match("adset", r["term"], r["med"]) or r["term"] or "(sem conjunto)",
            "ad": match("ad", r["content"]) or r["content"] or "(sem anúncio)",
            "src": r["src"] or "(sem UTM)",
            "st": r["st"] or "(sem status)", "per": r["per"] or "(não informado)",
            "disp": r["disp"] or "(não informado)", "pg": r["pg"] or "(não informada)",
            "utm": 1 if tem_utm else 0,
        })
        qk = (r["d"], r["queixa"] or "(não informada)")
        queixas[qk] = queixas.get(qk, 0) + 1
    return leads, [{"d": d, "q": q, "n": n} for (d, q), n in sorted(queixas.items())]


# --------------------------------------------------------------------------- #
# Processamento -> registros brutos
# --------------------------------------------------------------------------- #
def process(meta_rows, lp_keys=frozenset()):
    """lp_keys: campanhas (nkey) do funil LP — suas linhas do Meta Ads vão para
    lp_meta e NÃO entram em meta[]/leads[] (abas de WhatsApp ficam só com o
    funil de conversas)."""
    mheader = meta_rows[0] if meta_rows else []
    midx = header_index(
        mheader,
        {"day": ["day", "data"], "campaign": ["campaign name", "campaign"], "adset": ["ad set name", "adset"],
         "ad": ["ad name"], "spent": ["amount spent", "valor gasto", "gasto"], "impr": ["impressions", "impress"],
         "clicks": ["link clicks", "clicks", "cliques"],
         "leads": ["messaging conversations started", "leads"],
         "pv": ["landing page views", "page views", "pageviews"],
         "chk": ["adds to cart", "add to cart", "initiate checkout", "checkouts iniciados", "checkouts"],
         # Link do criativo — coluna opcional, não existe nesta planilha.
         "link": ["creative instagram permalink", "instagram permalink", "permalink",
                  "creative link", "link do anuncio", "link do criativo"]},
        {"day": 0, "campaign": 1, "adset": 2, "ad": 3, "impr": 4, "clicks": 5, "spent": 6, "leads": 7},
    )

    meta = []
    leads = []
    lp_meta = []
    ad_links = {}
    for row in meta_rows[1:]:
        if not any((c or "").strip() for c in row):
            continue
        d = parse_date(cell(row, midx["day"]))
        camp = cell(row, midx["campaign"]) or "(sem campanha)"
        adset = cell(row, midx["adset"]) or "(sem conjunto)"
        ad = cell(row, midx["ad"]) or "(sem anúncio)"
        link = cell(row, midx["link"])
        if link and ad not in ad_links:
            ad_links[ad] = link
        n_leads = to_float(cell(row, midx["leads"]))
        rec = {
            "d": d,
            "camp": camp,
            "adset": adset,
            "ad": ad,
            "sp": round(to_float(cell(row, midx["spent"])), 4),
            "im": to_float(cell(row, midx["impr"])),
            "cl": to_float(cell(row, midx["clicks"])),
            "pv": to_float(cell(row, midx["pv"])),
            "ck": to_float(cell(row, midx["chk"])),
        }
        if nkey(camp) in lp_keys:
            lp_meta.append(rec)
            continue
        meta.append(rec)
        # Leads sintéticos: 1 registro por conversa iniciada (Messaging
        # Conversations Started), sem segunda camada de qualificação nesta
        # conta — todo lead entra como q=1 (Leads == MQLs == 100%).
        for _ in range(round(n_leads)):
            leads.append({"d": d, "src": "meta", "camp": camp, "adset": adset, "ad": ad, "q": 1, "utm": 1})

    dates = sorted({d for d in ([l["d"] for l in leads if l["d"]] + [m["d"] for m in meta + lp_meta if m["d"]])})
    now_brt = datetime.now(BRT)
    return {
        "build": {
            "generated_at_brt": now_brt.strftime("%d/%m/%Y %H:%M"),
            "build_id": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
            "today": now_brt.strftime("%Y-%m-%d"),
            "date_min": dates[0] if dates else None,
            "date_max": dates[-1] if dates else None,
            "tax_factor": TAX_FACTOR,
            # config da aba Relatório (lida pelo front)
            "sample_min_spend": SAMPLE_MIN_SPEND,
            "sample_min_mqls": SAMPLE_MIN_MQLS,
            "top_ads_n": TOP_ADS_N,
            # metas & parâmetros (defaults do painel editável; None = não definida)
            "meta_cpmql": META_CPMQL,
            "volume_min_amostral": VOLUME_MIN_AMOSTRAL,
            "n_dias_corte": N_DIAS_CORTE,
        },
        "leads": leads,
        "meta": meta,
        # Funil landing page: gasto Meta das campanhas da LP (lp.leads/queixas
        # são preenchidos em main(), a partir da planilha de leads da LP).
        "lp": {"meta": lp_meta, "leads": [], "queixas": []},
        # Sem fonte de compradores nesta conta — Vendas/Faturamento ficam "-".
        "sales": [],
        # Sem coluna de permalink de criativo nesta planilha.
        "ad_links": ad_links,
        # Insights de Tráfego (texto pré-escrito, lido de relatorios.json). Preenchido
        # em main() via load_briefings(); fica {} se relatorios.json não existir.
        "briefings": {},
    }


# --------------------------------------------------------------------------- #
# Insights de Tráfego (aba Relatório)
# --------------------------------------------------------------------------- #
def load_briefings(path: str) -> dict:
    """Lê build/relatorios.json. Estrutura:
        {"generated_at": "...", "periodos": {"<preset>": {"html": "..."}, ...}}
    Retorna o dict inteiro (ou {} se o arquivo não existir/for inválido).
    A geração NÃO acontece aqui — este build só lê o texto já pronto, sem
    chamar nenhuma API (custo zero no build/no navegador)."""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except (ValueError, OSError):
        return {}


# --------------------------------------------------------------------------- #
# Render
# --------------------------------------------------------------------------- #
def render(data, template_path):
    # A dashboard e montada a partir de arquivos separados (visual x logica):
    #   template.html          -> esqueleto HTML (placeholders __STYLES__/__APP_JS__)
    #   identidade-visual.css  -> TODAS as cores (edite aqui p/ mexer so em cor)
    #   estilos.css            -> layout/componentes
    #   app.js                 -> logica + renderizacao
    # Esta funcao so COSTURA os arquivos e injeta os dados; nao altera nada deles.
    base = os.path.dirname(os.path.abspath(template_path))

    def readf(name):
        with open(os.path.join(base, name), "r", encoding="utf-8") as f:
            return f.read()

    with open(template_path, "r", encoding="utf-8") as f:
        tpl = f.read()
    styles = readf("identidade-visual.css") + "\n" + readf("estilos.css")
    tpl = tpl.replace("__STYLES__", styles)
    tpl = tpl.replace("__APP_JS__", readf("app.js"))
    tpl = tpl.replace("__DATA_JSON__", json.dumps(data, ensure_ascii=False))
    tpl = tpl.replace("__BUILD_ID__", data["build"]["build_id"])
    tpl = tpl.replace("__GENERATED_BRT__", data["build"]["generated_at_brt"])
    return tpl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--meta-file", help="CSV local da aba de Meta Ads (Página 1)")
    ap.add_argument("--seg-file", help="CSV local da aba mensal de Controle de tráfego (bloco META — Seguidores)")
    ap.add_argument("--lp-file", help="CSV local da aba Leads da planilha do funil landing page")
    ap.add_argument("--template", default="build/template.html")
    ap.add_argument("--out", default="dist/index.html")
    args = ap.parse_args()

    meta_rows = load_rows(EXPORT_URL.format(sid=SPREADSHEET_ID, gid=GID_META), args.meta_file)

    lp_raw = load_lp_leads(args.lp_file)
    data = process(meta_rows, lp_campaign_keys(lp_raw))
    data["lp"]["leads"], data["lp"]["queixas"] = build_lp(lp_raw, data["lp"]["meta"])
    data["seg"] = load_seguidores(args.seg_file, datetime.now(BRT))
    # "Todo período" cobre também dias de seguidores com movimento (dias futuros
    # já pré-listados na aba, ainda zerados, não esticam o intervalo).
    b = data["build"]
    dias = [x for x in (b["date_min"], b["date_max"]) if x] + \
           [s["d"] for s in data["seg"] if s["n"] or s["sp"]] + \
           [l["d"] for l in data["lp"]["leads"]]
    if dias:
        b["date_min"], b["date_max"] = min(dias), max(dias)

    # Insights de Tráfego (texto pré-escrito) — lidos do arquivo versionado ao
    # lado do template. Sem chamada de API no build.
    briefings_path = os.path.join(os.path.dirname(os.path.abspath(args.template)), "relatorios.json")
    data["briefings"] = load_briefings(briefings_path)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(render(data, args.template))

    b = data["build"]
    q = sum(l["q"] for l in data["leads"])
    print("== build ok ==", file=sys.stderr)
    print(f"  periodo   : {b['date_min']} -> {b['date_max']}", file=sys.stderr)
    print(f"  leads     : {len(data['leads'])}  (conversas iniciadas)  qualificados: {q}", file=sys.stderr)
    print(f"  meta      : {len(data['meta'])} linhas", file=sys.stderr)
    seg = data["seg"]
    print(f"  seguidores: {len(seg)} dias  total {sum(s['n'] for s in seg):.0f}  "
          f"invest R$ {sum(s['sp'] for s in seg):,.2f}", file=sys.stderr)
    hoje = datetime.now(BRT).strftime("%Y-%m-%d")
    ultimos = [s for s in seg if s["d"] <= hoje][-5:]
    if ultimos:
        print("  seguidores últimos dias: " + " | ".join(
            f"{s['d'][8:]}/{s['d'][5:7]} {s['n']:.0f} (R$ {s['sp']:.2f})" for s in ultimos), file=sys.stderr)
    lp = data["lp"]
    lp_camps = sorted({m["camp"] for m in lp["meta"]})
    print(f"  lp        : {len(lp['leads'])} leads ({sum(l['utm'] for l in lp['leads'])} c/ UTM)  "
          f"gasto R$ {sum(m['sp'] for m in lp['meta']):,.2f}  campanhas: {lp_camps or '-'}", file=sys.stderr)
    com_gasto = {nkey(c) for c in lp_camps}
    sem_gasto = sorted({r["camp"] for r in lp_raw if r["camp"] and nkey(r["camp"]) not in com_gasto})
    if sem_gasto:
        print(f"  AVISO lp: utm_campaign sem campanha correspondente no Meta Ads: {sem_gasto}", file=sys.stderr)
    print(f"  out       : {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
