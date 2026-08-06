# L2 FreeRoute model bakeoff

Question: `rank carriers by on-time percentage for hazmat only`
Winner (promoted): **`auto`**

## FreeRoute probe (SELECT 1)

| model | http | upstream | ms |
|---|---:|---|---:|
| `auto` | 200 | mistral-small-latest | 8101.9 |
| `mistral-small-latest` | 400 | - | 2783.0 |
| `ministral-8b-latest` | 400 | - | 5523.5 |
| `gemini-flash-latest` | 400 | - | 3884.4 |
| `gemini-2.5-flash` | 400 | - | 3095.3 |
| `google/gemini-2.5-flash` | 400 | - | 1965.4 |
| `llama-3.3-70b-versatile` | 400 | - | 7255.2 |
| `openai/gpt-oss-20b` | 400 | - | 5810.0 |

## L2 hazmat ask

| model | badge | abstain | ms | sql |
|---|---|---|---:|---|
| `auto` | L2_VALIDATED | False | 11305.2 | `SELECT carrier, ROUND(100.0 * SUM(CASE WHEN status = 'DELIVE` |

## Promote

```powershell
D:\DMS\scripts\windows\Start-DMSStack.ps1 -StartSiblings -EnableL2 -L2Model auto
# or: $env:DMS_L2_MODEL='auto'
```

Generator default remains `auto` if unset; launcher `-L2Model` pins **auto**.
