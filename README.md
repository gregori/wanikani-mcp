# wanikani-mcp

Servidor MCP **somente leitura** com o progresso no WaniKani, para o Claude
calibrar exercícios de kanji e vocabulário. Roda no **Vercel (plano Hobby,
gratuito)** como função Python; o deploy acontece a cada push na `main` pela
integração do Vercel com o GitHub.

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
- `MCP_ALLOWED_HOSTS` (opcional) restringe o header `Host`.

## Deploy no Vercel

1. Entrar em [vercel.com](https://vercel.com) com a conta do GitHub (plano Hobby).
2. **Add New → Project → Import** `gregori/wanikani-mcp`. Framework: deixe
   o Vercel detectar (Python); não precisa de build command.
3. Em **Environment Variables**, cadastrar:
   - `WANIKANI_API_TOKEN`: token read-only (sem nenhuma permissão marcada) em
     wanikani.com/settings/personal_access_tokens
   - `MCP_PATH_SECRET`: `python -c "import secrets; print(secrets.token_urlsafe(32))"`
4. **Deploy**. Conferir `https://<projeto>.vercel.app/healthz` → `ok`.
   Se aparecer uma tela de login do Vercel, desligue a *Deployment
   Protection* para Production em Settings → Deployment Protection.
5. No claude.ai: **Configurações → Conectores → Adicionar conector
   personalizado**, URL `https://<projeto>.vercel.app/mcp/<MCP_PATH_SECRET>`.

Se faltar alguma variável, o servidor responde 503 explicando qual.

## Desenvolvimento

```bash
pip install -e ".[dev]"
pytest -q
MCP_PATH_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))") \
WANIKANI_API_TOKEN=... wanikani-mcp
```
