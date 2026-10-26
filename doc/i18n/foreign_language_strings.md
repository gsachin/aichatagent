# Foreign-language strings — review sheet

The six non-Indian languages are Spanish (`es`), French (`fr`), Italian (`it`),
Japanese (`ja`), Portuguese (`pt`) and Chinese (`zh`). Their strings live in one
place, `app/localized_text.py`; English, Hindi and Gujarati stay in their
original modules so an additive change is visible at review time.

**Review requirement:** a native speaker for each language should initial the
table below before a client demo. Machine-authored translations can be
grammatical and still sound wrong on a phone call (register, gendered forms,
digits read aloud).

## Review checklist

| Item | Why it matters |
|---|---|
| Female speaker | The agent is female. French, Italian, Portuguese and Spanish adjectives/participles must agree; Japanese/Chinese should stay neutral-polite. |
| Phone register | Short sentences, no headings, no Markdown, no internal vocabulary ("engine", "timeout", "model"). |
| Placeholders | `{company_name}`, `{agent_name}`, `{subject}`, `{subject_cap}`, `{name}`, `{email}`, `{phone}`, `{prog}`, `{reqs}` must survive every translation. |
| Numbers and currency | Numbers are spoken as digits/currency; the grounding boundary still checks them. |
| Brand and degrees | `DSPMU`, `B.Tech`, `MBA`, `B.CA`, `B.Com` stay in Latin script. |
| Japanese/Chinese | No spaces between characters; use `。！？`; Arabic numerals. Japanese and Chinese use the misaki G2P (not espeak-ng). |
| TTS-only readings | For ja/zh, `app/voice_handler._TTS_READINGS` substitutes `DSPMU` and common degree abbreviations for a spoken reading (e.g. `経営学修士`, `工商管理硕士`). Review those substitutions too; they affect audio only, not the transcript. |
| Names | `{subject_cap}` has no capitalisation in Japanese/Chinese; the formatter's `.upper()` is a no-op, so the same text is used. |

## Sets to review

| Set | Location | Used for |
|---|---|---|
| `INBOUND_GREETINGS`, `OUTBOUND_GREETINGS` | `app/localized_text.py` | First sentence of every call |
| `NOISE_LADDERS` | `app/localized_text.py` | Retry → line quality → alternate channel → callback |
| `CLOSING_REPLIES` | `app/localized_text.py` | Deterministic sign-off |
| `FIXED_RESPONSE_TEXTS`, `BUSY_TEXTS` | `app/localized_text.py` | Pre-recorded failure/refusal audio |
| `GROUNDING_REFUSALS` | `app/localized_text.py` | When the grounding boundary refuses an answer |
| `WHATSAPP_LANGUAGE_CONFIRMATIONS`, `WHATSAPP_LANGUAGE_HELP` | `app/localized_text.py` | Language command feedback |
| `STREAMLIT_UI` | `app/localized_text.py` | Streamlit onboarding prompts |

## Critical strings (copy for review)

### Greetings

| | Inbound | Outbound |
|---|---|---|
| es | Hola, soy la asistente de admisiones. Pregúnteme lo que quiera sobre los programas, las matrículas o cómo solicitar admisión a {company_name}. | Hola, le habla {agent_name} de Admisiones de {company_name}. ¿Tiene un momento para hablar sobre nuestros programas, matrículas o cómo solicitar admisión? |
| fr | Bonjour, je suis l'assistante des admissions. Posez-moi toutes vos questions sur les programmes, les frais de scolarité ou les modalités de candidature de {company_name}. | Bonjour, ici {agent_name}, du service des admissions de {company_name}. Avez-vous un instant pour parler de nos programmes, des frais de scolarité ou des modalités de candidature ? |
| it | Ciao, sono l'assistente per le ammissioni. Mi chieda pure informazioni sui programmi, sulle tasse universitarie o su come iscriversi a {company_name}. | Buongiorno, sono {agent_name} dell'ufficio ammissioni di {company_name}. Ha un momento per parlare dei nostri programmi, delle tasse o di come iscriversi? |
| ja | こんにちは、入学アシスタントです。{company_name}のプログラム、学費、出願方法について何でもお尋ねください。 | {company_name}入学課の{agent_name}です。プログラム、学費、出願方法についてお話しするお時間はありますか？ |
| pt | Olá, sou a assistente de admissões. Pergunte o que quiser sobre os programas, as mensalidades ou como se inscrever na {company_name}. | Olá, sou {agent_name}, do setor de admissões da {company_name}. Você tem um momento para falar sobre nossos programas, mensalidades ou como se inscrever? |
| zh | 您好，我是招生助理。有关{company_name}的课程、学费或申请方式，欢迎随时问我。 | 您好，我是{company_name}招生办的{agent_name}。您现在方便聊聊我们的课程、学费或申请方式吗？ |

### Closing

| | |
|---|---|
| es | Gracias por llamar a Admisiones de DSPMU. ¡Que tenga un buen día! |
| fr | Merci d'avoir appelé les admissions de DSPMU. Bonne journée ! |
| it | Grazie per aver chiamato l'ufficio ammissioni della DSPMU. Buona giornata! |
| ja | DSPMU 入学課にお電話いただき、ありがとうございました。よい一日をお過ごしください。 |
| pt | Obrigada por ligar para o setor de admissões da DSPMU. Tenha um ótimo dia! |
| zh | 感谢您致电 DSPMU 招生办。祝您愉快！ |

### Failure / busy

| | Fixed response | Busy |
|---|---|---|
| es | Lo siento, no le entendí bien. ¿Podría repetirlo, por favor? | Gracias por llamar a Admisiones de la DSPMU. Todos nuestros asesores están en llamada en este momento. Vuelva a intentarlo en unos minutos y con gusto le ayudaremos. |
| fr | Je suis désolée, je n'ai pas bien compris. Pourriez-vous répéter, s'il vous plaît ? | Merci d'avoir appelé les admissions de DSPMU. Tous nos conseillers sont en ligne pour le moment. Réessayez dans quelques minutes, nous serons ravis de vous aider. |
| it | Mi scuso, non ho capito bene. Può ripetere, per favore? | Grazie per aver chiamato l'ufficio ammissioni della DSPMU. Tutti i nostri consulenti sono al telefono in questo momento. Riprovi tra qualche minuto, saremo felici di aiutarla. |
| ja | すみません、よく聞き取れませんでした。もう一度お願いします。 | DSPMU 入学課です。ただいま電話対応中です。後ほどおかけ直しください。 |
| pt | Desculpe, não entendi bem. Pode repetir, por favor? | Obrigada por ligar para o setor de admissões da DSPMU. Todos os nossos consultores estão em ligação no momento. Tente novamente em alguns minutos e teremos prazer em ajudar. |
| zh | 抱歉，我刚才没听清楚。请您再说一遍好吗？ | 感谢您致电 DSPMU 招生办。我们的顾问目前都在通话中。请过几分钟再拨，我们将很乐意为您服务。 |

The remaining sets (`NOISE_LADDERS`, `GROUNDING_REFUSALS`, `STREAMLIT_UI`,
WhatsApp confirmations) are in `app/localized_text.py` and should be reviewed in
place. Keep this sheet and the module in sync when wording changes.
