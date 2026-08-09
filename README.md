# cloud-publisher — publicação private→public na nuvem (PC desligado)

Publica os vídeos do YouTube no horário-alvo **sem depender do seu PC ligado**.
Um cron na nuvem (GitHub Actions, grátis) roda a cada 10 min, olha a agenda e troca
os vídeos vencidos de `private` para `public` via API do Composio. Idempotente: o
`state.json` guarda o que já publicou, então nunca republica.

> Substitui as 32 tarefas locais `publicar-*` (que exigiam o PC ligado). Só a
> **publicação** vai pra nuvem; a **criação** (esteira) continua no PC.

## Arquivos
- `schedule.json` — a agenda: `video_id`, `account_id`, `publish_at_utc` de cada peça (416 itens, dias 10-22). Gerado do manifesto.
- `gen_schedule.py` — regenera `schedule.json` do manifesto (rode ao adicionar novos dias).
- `publish_due.py` — o runner (só stdlib, sem `pip install`).
- `state.json` — o que já foi publicado (o Actions commita de volta a cada rodada).
- `.github/workflows/publish.yml` — o cron da nuvem (a cada 10 min).

## Pré-requisito (só você consegue fazer): consumer key do Composio
O runner autentica no **MCP hospedado do Composio** (`https://connect.composio.dev/mcp`) com a
**consumer key** (a que começa com `ck_`, enviada no header `x-consumer-api-key` — a mesma que os
clientes MCP usam). Pegue no painel do Composio, na página do seu servidor MCP.
Essa key publica seus vídeos, então **não coloque no código** — vai como *secret*.

> Obs.: NÃO é a "Project API key" (`x-api-key`, para a REST) — o runner fala via MCP, não REST.

## Setup (uma vez, ~5 min)
1. Crie um repositório **privado** no GitHub (ex.: `cloud-publisher`).
2. Suba o conteúdo desta pasta pra raiz do repo (na branch `main`):
   ```bash
   cd "cloud-publisher"
   git init && git add . && git commit -m "cloud-publisher"
   git branch -M main
   git remote add origin git@github.com:SEU_USUARIO/cloud-publisher.git
   git push -u origin main
   ```
3. No repo: **Settings → Secrets and variables → Actions → New repository secret**
   - Nome: `COMPOSIO_CONSUMER_KEY`  •  Valor: a consumer key `ck_...`.
4. **Settings → Actions → General → Workflow permissions →** marque **Read and write permissions** (pro workflow commitar o `state.json`).
5. Pronto. O workflow roda sozinho a cada 10 min. Pra testar na hora: aba **Actions → publicar-youtube-agendado → Run workflow**.

## Validar ANTES de confiar (sem publicar nada cedo)  — JÁ VALIDADO EM 09/08/2026
Rode localmente (ou no Actions manual) com a key exportada:
```bash
export COMPOSIO_CONSUMER_KEY=ck_xxx
python3 publish_due.py --check-auth              # initialize+tools/list (confirma a credencial)
python3 publish_due.py --test-execute <VIDEO_ID> # testa o caminho de execute SEM efeito (no-op)
python3 publish_due.py --dry-run                 # mostra o que faria hoje
```
`--test-execute` chama `YOUTUBE_UPDATE_VIDEO` só com `video_id` (sem mudar privacidade),
então prova que MCP+auth+ferramenta funcionam sem publicar nada antes da hora.
(Os três já passaram nesta máquina: AUTH OK 7 tools, no-op validado, dry-run 0 vencidos.)

## Observações
- **Precisão:** o cron do GitHub pode atrasar ~5-15 min sob carga. Pra vídeo, ok.
- **DST:** os horários já saem em UTC com horário de verão correto (melhor que o cron local antigo).
- **Ativação do cron:** o Actions desativa cron de repo após 60 dias sem atividade; como o workflow commita `state.json` a cada publicação, ele se mantém ativo durante a campanha.
- **Novos dias (23+):** rode `python3 gen_schedule.py 2026-08-23 2026-08-29` e dê commit no `schedule.json` atualizado.
- **Transporte:** o runner fala com o MCP hospedado (`COMPOSIO_MCP_URL`, default `https://connect.composio.dev/mcp`) via JSON-RPC/Streamable-HTTP, chamando `COMPOSIO_MULTI_EXECUTE_TOOL` → `YOUTUBE_UPDATE_VIDEO` na conta certa. Só stdlib, sem SDK.
- **Desligar o cron local:** só remova/disable as 32 tarefas `publicar-*` DEPOIS de confirmar 1 ciclo na nuvem (pra não ficar sem publicar nem publicar em dobro).
