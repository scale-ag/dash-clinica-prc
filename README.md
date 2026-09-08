# Dashboard de Tráfego Pago · Clínica PRC

Dashboard **100% na nuvem** de mídia paga da **Clínica PRC**, que lê a aba
"Página 1" (Meta Ads) da planilha do cliente, calcula **Leads** a partir da
coluna nativa **Messaging Conversations Started** (conversas de WhatsApp
iniciadas) e é publicada no **GitHub Pages**. Reconstrói sozinha a cada
~30 min, disparada pelo **cron-job.org** — sem depender de nenhum PC ligado.

**URL pública:** `https://scale-ag.github.io/dash-clinica-prc/`

---

## O que ela mostra

- **KPIs**: Gasto Total, Impressões, Cliques, Leads (conversas iniciadas), CPL, MQLs, CPMQL, Tx-MQL, CTR, CPC, CPM.
- **Evolução diária**: gasto/dia, leads/dia, CPL × CPMQL/dia.
- **Distribuição de leads**: por origem, por público (conjunto de anúncios), por dia da semana e por campanha.
- **Hierarquia Campanha → Conjunto → Anúncio**: gasto, leads, CPL, CPMQL por nível, com gráfico de custo por dia.
- **Toggle de imposto da mídia paga** e **modo claro/escuro**.
- **Aba Relatório**: espelha a Visão Geral + painel de metas editável + Top/Piores Anúncios + Insights de Tráfego (texto, opcional — ver `build/GUIA-RELATORIOS.md`).

## Critério de "MQL" nesta conta

Esta conta **não tem uma segunda camada de qualificação** — só a coluna
`Messaging Conversations Started` do Meta Ads. Por isso, nesta dashboard,
**Leads = MQLs = 100%** (todo lead sintetizado em `build.py` já entra com
`q=1`). **Vendas/Faturamento/CAC/ROAS** não têm fonte conectada (sem lista de
compradores) e aparecem como "-" em toda a dashboard.

## Fontes de dados (somente leitura)

Planilha de Meta Ads da Clínica PRC
(`1SrzEB16RhXoQm28tNRF4TcUmhYqaTJZBlq5xQCCRkLE`):

| Aba | gid | Colunas usadas |
|-----|-----|----------------|
| Página 1 (Meta Ads) | `0` | `Day` · `Campaign Name` · `Ad Set Name` · `Ad Name` · `Impressions` · `Link Clicks` · `Amount Spent` · `Messaging Conversations Started` |

O build lê essa aba via **export CSV público** (`.../export?format=csv&gid=0`).
**Nada é escrito de volta** na planilha.

---

## Arquitetura

```
cron-job.org  ──(POST workflow_dispatch a cada 30 min)──▶  GitHub Actions
                                                              │
                          build/build.py  lê o CSV ◀──────────┘
                                 │  gera leads sintéticos + agrega
                                 ▼
                          dist/index.html  ──▶  deploy  ──▶  GitHub Pages (URL pública)
```

- `build/build.py` — baixa o CSV do Meta Ads, gera `dist/index.html`.
- `build/template.html` — layout/gráficos/tema (Chart.js via CDN).
- `.github/workflows/deploy.yml` — roda o build e publica no Pages.

**Cache-bust:** a página usa `Cache-Control: no-cache`, mostra o horário do último
build, tem botão **Atualizar** e se recarrega sozinha (`?t=timestamp`) ~30 min após
aberta — sempre pegando a versão mais nova.

## Rodar localmente (opcional)

```bash
python build/build.py --out dist/index.html            # busca o CSV ao vivo
# ou, com arquivo local para teste (o sandbox de agentes não alcança docs.google.com):
python build/build.py --meta-file meta.csv --out dist/index.html
```

---

## Ativação (uma vez) e cron-job.org

O disparo por `workflow_dispatch` só funciona quando o workflow está na branch
**`main`**. Veja **`SETUP-CRON.md`** para o passo a passo e os valores exatos
(URL, headers e body, com marcador do token a preencher) a colar no cron-job.org.

> ⚠️ **Segurança:** nunca comite tokens no repositório. Gere um token
> *fine-grained*, só com **Actions: read/write** neste repositório, e use-o
> apenas no cron-job.org.

## Como usar este template para outro cliente

Veja o **CHECKLIST DE NOVO CLIENTE** no topo de `CLAUDE.md` (ou `AGENTS.md`) e
o passo a passo completo em `GUIA-REPLICACAO.md`.
