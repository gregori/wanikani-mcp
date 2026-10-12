# wanikani-mcp

Servidor MCP **somente leitura** com o progresso no WaniKani, para o Claude
calibrar exercícios de kanji e vocabulário. Roda no k3s da OCI no mesmo
padrão do `novel-translator` (OCIR, Traefik, TLS do cert-manager, deploy
por SSH).

## Ferramentas

| Ferramenta | O que faz |
|---|---|
| `get_progress` | nível, kanji/vocabulário por estágio SRS, revisões e lições pendentes |
| `get_leeches(top, include_burned)` | itens com mais erros |
| `sample_burned(n, subject_type, min_level, max_level)` | sorteia itens burned para testar retenção |
| `lookup(text)` | status de uma palavra/kanji e dos kanji que a compõem |

Cache em memória: catálogo de subjects por 24 h, dados do usuário por 10 min.

## Segurança

- Token do WaniKani **read-only**; o servidor só faz GET.
- Endpoint em `/mcp/<MCP_PATH_SECRET>`; qualquer outro caminho dá 404.
  `/healthz` é público para as probes.
- `MCP_ALLOWED_HOSTS` restringe o header `Host` (proteção contra DNS rebinding).

## Secrets do GitHub

| Secret | Origem |
|---|---|
| `K8S_NODE_PUBLIC_IP`, `OCI_SSH_PRIVATE_KEY` | mesmos do novel-translator |
| `OCI_REGISTRY`, `OCI_REGISTRY_USERNAME`, `OCI_REGISTRY_PASSWORD`, `OCI_TENANCY_NAMESPACE` | mesmos do novel-translator |
| `WANIKANI_API_TOKEN` | token read-only em wanikani.com/settings/personal_access_tokens |
| `MCP_PATH_SECRET` | `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `INGRESS_HOST` | ex. `wk.gregori.eti.br` (DNS apontando para o nó) |

## Primeiro deploy

1. Apontar `INGRESS_HOST` no DNS para o IP público do nó.
2. Cadastrar os secrets acima.
3. Push na `main` (ou `workflow_dispatch` em `CD - Deploy`).
4. Conferir `https://<INGRESS_HOST>/healthz` → `ok`.
5. No claude.ai: **Configurações → Conectores → Adicionar conector
   personalizado**, URL `https://<INGRESS_HOST>/mcp/<MCP_PATH_SECRET>`.

## Desenvolvimento

```bash
pip install -e ".[dev]"
pytest -q
MCP_PATH_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))") \
WANIKANI_API_TOKEN=... wanikani-mcp
```
