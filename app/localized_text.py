"""Localized fixed strings, kept apart from the English constants.

The six non-Indian languages live here, and so does every Streamlit
onboarding prompt. English keeps its literals in ``app.py``; Hindi and
Gujarati have entries here as well, because the Streamlit selector offers
them and the fallback-to-English behaviour was a defect: selecting Hindi
still asked for the name, email and phone in English.

The voice greetings, noise ladders, closing lines and grounding refusals for
the six foreign languages also live here.
"""

from __future__ import annotations

from app.languages import normalize_language_code

#: Reply to a whole-message language command. The command itself is parsed in
#: ``app.languages.language_from_command``; this is only the confirmation.
WHATSAPP_LANGUAGE_CONFIRMATIONS = {
    "en": "Got it — I'll continue in English.",
    "hi": "ठीक है — मैं हिंदी में जारी रखूँगी।",
    "gu": "ઠીક છે — હું ગુજરાતીમાં ચાલુ રાખીશ.",
    "es": "Perfecto, seguiré en español.",
    "fr": "Parfait, je continue en français.",
    "it": "Perfetto, continuo in italiano.",
    "ja": "日本語で続けます。",
    "pt": "Perfeito, vou continuar em português.",
    "zh": "好的，我会继续用中文。",
}

#: Sent when a language choice arrives after a restart and the original
#: question is no longer in memory. The confirmation is followed by this line.
WHATSAPP_LANGUAGE_RESEND = {
    "en": "Please send your question again.",
    "hi": "कृपया अपना सवाल फिर से भेजिए।",
    "gu": "કૃપા કરીને તમારો પ્રશ્ન ફરી મોકલો.",
    "es": "Por favor, envía tu pregunta de nuevo.",
    "fr": "Veuillez renvoyer votre question.",
    "it": "Per favore, invia di nuovo la tua domanda.",
    "ja": "もう一度ご質問をお送りください。",
    "pt": "Por favor, envie sua pergunta novamente.",
    "zh": "请重新发送您的问题。",
}

#: Fixed closing line for the deterministic hangup path, in each foreign
#: language. English keeps its existing constant in app/voice_handler.py.
CLOSING_REPLIES = {
    "es": "Gracias por llamar a Admisiones de Meridian University. ¡Que tenga un buen día!",
    "fr": "Merci d'avoir appelé les admissions de Meridian University. Bonne journée !",
    "it": "Grazie per aver chiamato l'ufficio ammissioni della Meridian University. Buona giornata!",
    "ja": (
        "Meridian University 入学課にお電話いただき、ありがとうございました。"
        "よい一日をお過ごしください。"
    ),
    "pt": "Obrigada por ligar para o setor de admissões da Meridian University. Tenha um ótimo dia!",
    "zh": "感谢您致电 Meridian University 招生办。祝您愉快！",
}

#: Opening scripts. `call_greeting` formats these with agent_name/company_name.
INBOUND_GREETINGS = {
    "es": (
        "Hola, soy la asistente de admisiones. Pregúnteme lo que quiera sobre "
        "los programas, las matrículas o cómo solicitar admisión a "
        "{company_name}."
    ),
    "fr": (
        "Bonjour, je suis l'assistante des admissions. Posez-moi toutes vos "
        "questions sur les programmes, les frais de scolarité ou les modalités "
        "de candidature de {company_name}."
    ),
    "it": (
        "Ciao, sono l'assistente per le ammissioni. Mi chieda pure "
        "informazioni sui programmi, sulle tasse universitarie o su come "
        "iscriversi a {company_name}."
    ),
    "ja": (
        "こんにちは、入学アシスタントです。{company_name}のプログラム、学費、"
        "出願方法について何でもお尋ねください。"
    ),
    "pt": (
        "Olá, sou a assistente de admissões. Pergunte o que quiser sobre os "
        "programas, as mensalidades ou como se inscrever na {company_name}."
    ),
    "zh": (
        "您好，我是招生助理。有关{company_name}的课程、学费或申请方式，"
        "欢迎随时问我。"
    ),
}

OUTBOUND_GREETINGS = {
    "es": (
        "Hola, le habla {agent_name} de Admisiones de {company_name}. ¿Tiene "
        "un momento para hablar sobre nuestros programas, matrículas o cómo "
        "solicitar admisión?"
    ),
    "fr": (
        "Bonjour, ici {agent_name}, du service des admissions de "
        "{company_name}. Avez-vous un instant pour parler de nos programmes, "
        "des frais de scolarité ou des modalités de candidature ?"
    ),
    "it": (
        "Buongiorno, sono {agent_name} dell'ufficio ammissioni di "
        "{company_name}. Ha un momento per parlare dei nostri programmi, "
        "delle tasse o di come iscriversi?"
    ),
    "ja": (
        "{company_name}入学課の{agent_name}です。プログラム、学費、出願方法に"
        "ついてお話しするお時間はありますか？"
    ),
    "pt": (
        "Olá, sou {agent_name}, do setor de admissões da {company_name}. Você "
        "tem um momento para falar sobre nossos programas, mensalidades ou "
        "como se inscrever?"
    ),
    "zh": (
        "您好，我是{company_name}招生办的{agent_name}。您现在方便聊聊我们的"
        "课程、学费或申请方式吗？"
    ),
}

#: Four escalating noise/timeout lines per language. The existing English,
#: Hindi and Gujarati ladders stay in app/voice_handler.py.
NOISE_LADDERS = {
    "es": [
        "Perdón, no le entendí bien. ¿Podría repetirlo?",
        "Parece que la línea no se escucha bien. ¿Podría hablar un poco más "
        "cerca del teléfono?",
        "La conexión no está mejorando. También puede escribirnos por WhatsApp "
        "o enviar un correo a la oficina de admisiones.",
        "Lo siento, sigo sin escucharle bien. Pediré a un asesor que le "
        "devuelva la llamada. ¿Podría decirme otra vez su número?",
    ],
    "fr": [
        "Désolée, je n'ai pas bien compris. Pourriez-vous répéter ?",
        "La ligne n'est pas très claire. Pourriez-vous parler un peu plus près "
        "du téléphone ?",
        "La connexion ne s'améliore pas. Vous pouvez aussi nous écrire sur "
        "WhatsApp ou envoyer un e-mail au service des admissions.",
        "Je suis désolée, je ne vous entends toujours pas bien. Je vais "
        "demander à un conseiller de vous rappeler. Pourriez-vous me redonner "
        "votre numéro ?",
    ],
    "it": [
        "Mi scusi, non ho capito bene. Può ripetere?",
        "La linea non è molto chiara. Può parlare un po' più vicino al "
        "telefono?",
        "La connessione non migliora. Può anche scriverci su WhatsApp o "
        "inviare un'e-mail all'ufficio ammissioni.",
        "Mi dispiace, continuo a non sentirla bene. Chiederò a un consulente "
        "di richiamarla. Può dirmi di nuovo il suo numero?",
    ],
    "ja": [
        "すみません、よく聞き取れませんでした。もう一度おっしゃっていただけますか。",
        "電話の音が少し聞き取りにくいようです。電話に少し近づいてお話しいただけますか。",
        "接続が改善していません。WhatsAppでメッセージを送るか、入学課にメールで"
        "ご連絡いただくこともできます。",
        "申し訳ありません、まだよく聞こえません。担当者から折り返しお電話するよう"
        "お伝えします。お電話番号をもう一度教えていただけますか。",
    ],
    "pt": [
        "Desculpe, não entendi bem. Pode repetir, por favor?",
        "A linha não está muito clara. Pode falar um pouco mais perto do "
        "telefone?",
        "A conexão não está melhorando. Você também pode nos enviar uma "
        "mensagem pelo WhatsApp ou um e-mail para o escritório de admissões.",
        "Desculpe, ainda não consigo ouvir bem. Vou pedir para um consultor "
        "ligar de volta. Pode me dizer seu número novamente?",
    ],
    "zh": [
        "抱歉，我没听清楚。请您再说一遍好吗？",
        "线路好像不太清楚。请您靠近话筒一点说话，好吗？",
        "连接没有改善。您也可以通过 WhatsApp 给我们发消息，或发邮件到招生办公室。",
        "抱歉，我还是听不太清楚。我会请顾问给您回电。请您再说一遍您的电话号码好吗？",
    ],
}

#: Pre-recorded fallback wording. English stays in app/admission.py.
FIXED_RESPONSE_TEXTS = {
    "es": "Lo siento, no le entendí bien. ¿Podría repetirlo, por favor?",
    "fr": "Je suis désolée, je n'ai pas bien compris. Pourriez-vous répéter, s'il vous plaît ?",
    "it": "Mi scuso, non ho capito bene. Può ripetere, per favore?",
    "ja": "すみません、よく聞き取れませんでした。もう一度お願いします。",
    "pt": "Desculpe, não entendi bem. Pode repetir, por favor?",
    "zh": "抱歉，我刚才没听清楚。请您再说一遍好吗？",
}

BUSY_TEXTS = {
    "es": (
        "Gracias por llamar a Admisiones de la Meridian University. Todos "
        "nuestros asesores están en llamada en este momento. Vuelva a "
        "intentarlo en unos minutos y con gusto le ayudaremos."
    ),
    "fr": (
        "Merci d'avoir appelé les admissions de Meridian University. Tous nos "
        "conseillers sont en ligne pour le moment. Réessayez dans quelques "
        "minutes, nous serons ravis de vous aider."
    ),
    "it": (
        "Grazie per aver chiamato l'ufficio ammissioni della Meridian "
        "University. Tutti i nostri consulenti sono al telefono in questo "
        "momento. Riprovi tra qualche minuto, saremo felici di aiutarla."
    ),
    "ja": (
        "Meridian University 入学課です。ただいま電話対応中です。"
        "後ほどおかけ直しください。"
    ),
    "pt": (
        "Obrigada por ligar para o setor de admissões da Meridian University. "
        "Todos os nossos consultores estão em ligação no momento. Tente "
        "novamente em alguns minutos e teremos prazer em ajudar."
    ),
    "zh": (
        "感谢您致电 Meridian University 招生办。我们的顾问目前都在通话中。"
        "请过几分钟再拨，我们将很乐意为您服务。"
    ),
}

#: Grounding refusal wording. English (the default) lives in
#: app/voice_grounding.py; only the six additions are here.
GROUNDING_REFUSALS = {
    "es": {
        "variants": (
            "No tengo ese dato a mano. Puedo consultarlo y responderle, o "
            "ayudarle con otra cosa sobre el programa.",
            "Eso no lo puedo confirmar con el perfil de la universidad. Puedo "
            "ayudarle con cualquier otra cosa sobre programas, tasas o "
            "solicitudes.",
            "No quiero adivinar eso. Lo que sí puedo hacer es explicarle lo "
            "que cubre el perfil.",
        ),
        "escalations": (
            "Eso es todo lo que el perfil de la universidad tiene sobre ese "
            "tema. La oficina de admisiones puede ayudarle más: "
            "admissions@meridian.edu o +1 (555) 204-7890, de lunes a viernes, "
            "de 9 a 5.",
            "Hasta aquí puedo llegar con ese tema, y prefiero decírselo antes "
            "que seguir dando vueltas. Admisiones puede ayudarle en "
            "admissions@meridian.edu o +1 (555) 204-7890. ¿Hay algo más en lo "
            "que pueda ayudarle?",
        ),
        "unoffered": (
            "Meridian University no ofrece {subject}. Puedo hablarle de los "
            "programas que sí ofrecemos o ponerle en contacto con la oficina "
            "de admisiones.",
            "{subject_cap} no es uno de los programas de Meridian University. "
            "Con gusto le explico lo que sí ofrecemos, o la oficina de "
            "admisiones puede ayudarle.",
        ),
        "unoffered_markers": ("no ofrece", "no es uno de los programas"),
    },
    "fr": {
        "variants": (
            "Je n'ai pas ce détail sous les yeux. Je peux me renseigner et "
            "revenir vers vous, ou vous aider sur un autre point du programme.",
            "Je ne peux pas le confirmer à partir du profil de l'université. "
            "Je peux vous aider sur autre chose concernant les programmes, les "
            "frais ou les candidatures.",
            "Je ne veux pas deviner. Ce que je peux faire, c'est vous "
            "expliquer ce que le profil couvre.",
        ),
        "escalations": (
            "C'est tout ce que le profil de l'université contient sur ce "
            "sujet. Le service des admissions peut aller plus loin : "
            "admissions@meridian.edu ou +1 (555) 204-7890, du lundi au "
            "vendredi, de 9 h à 17 h.",
            "Je ne peux pas aller plus loin sur ce point, et je préfère vous "
            "le dire plutôt que de tourner en rond. Les admissions peuvent "
            "vous aider à admissions@meridian.edu ou au +1 (555) 204-7890. "
            "Puis-je vous aider sur autre chose ?",
        ),
        "unoffered": (
            "Meridian University ne propose pas {subject}. Je peux vous parler "
            "des programmes que nous proposons ou vous mettre en contact avec "
            "le service des admissions.",
            "{subject_cap} ne fait pas partie des programmes de Meridian "
            "University. Je serais ravie de vous présenter ce que nous "
            "proposons, ou le service des admissions peut vous aider.",
        ),
        "unoffered_markers": ("ne propose pas", "ne fait pas partie des programmes"),
    },
    "it": {
        "variants": (
            "Non ho quel dettaglio sotto mano. Posso verificarlo e "
            "ricontattarla, oppure aiutarla su un altro punto del programma.",
            "Non posso confermarlo dal profilo dell'università. Posso aiutarla "
            "su altro riguardo ai programmi, alle tasse o alle iscrizioni.",
            "Preferisco non indovinare. Posso però spiegarle cosa copre il "
            "profilo.",
        ),
        "escalations": (
            "Questo è tutto ciò che il profilo dell'università riporta "
            "sull'argomento. L'ufficio ammissioni può seguirla: "
            "admissions@meridian.edu o +1 (555) 204-7890, dal lunedì al "
            "venerdì, dalle 9 alle 17.",
            "Non posso andare oltre su questo punto, e preferisco dirglielo "
            "invece di girare intorno. Le ammissioni possono aiutarla a "
            "admissions@meridian.edu o al +1 (555) 204-7890. Posso aiutarla in "
            "altro?",
        ),
        "unoffered": (
            "La Meridian University non offre {subject}. Posso parlarle dei "
            "programmi che offriamo o metterla in contatto con l'ufficio "
            "ammissioni.",
            "{subject_cap} non è uno dei programmi della Meridian University. "
            "Sarò lieta di illustrarle ciò che offriamo, oppure può aiutarla "
            "l'ufficio ammissioni.",
        ),
        "unoffered_markers": ("non offre", "non è uno dei programmi"),
    },
    "ja": {
        "variants": (
            "その詳細は今手元にありません。確認してお伝えするか、プログラムに"
            "ついて別のご質問をお手伝いできます。",
            "大学のプロフィールからは確認できません。プログラム、学費、出願に"
            "ついて、ほかのことでお手伝いできます。",
            "推測はしたくありません。プロフィールに載っている内容をご説明する"
            "ことはできます。",
        ),
        "escalations": (
            "大学のプロフィールにあるのはそこまでです。入学課がさらにご案内"
            "できます。admissions@meridian.edu または +1 (555) 204-7890"
            "（月曜〜金曜、9時〜17時）。",
            "この件はこれ以上お答えできません。同じことを繰り返すより、担当者に"
            "おつなぎするのがよいと思います。admissions@meridian.edu または "
            "+1 (555) 204-7890 までご連絡ください。ほかに何かお手伝いできますか？",
        ),
        "unoffered": (
            "Meridian University では{subject}は提供していません。提供している"
            "プログラムをご案内するか、入学課におつなぎできます。",
            "{subject_cap}は Meridian University のプログラムではありません。"
            "提供している内容をご案内できますし、入学課でもお手伝いできます。",
        ),
        "unoffered_markers": ("は提供していません", "のプログラムではありません"),
    },
    "pt": {
        "variants": (
            "Não tenho esse detalhe em mãos. Posso verificar e retornar, ou "
            "ajudar com outra coisa sobre o programa.",
            "Não consigo confirmar isso pelo perfil da universidade. Posso "
            "ajudar com outra coisa sobre programas, mensalidades ou "
            "inscrições.",
            "Não quero adivinhar. O que posso fazer é explicar o que o perfil "
            "cobre.",
        ),
        "escalations": (
            "Isso é tudo o que o perfil da universidade tem sobre o assunto. O "
            "setor de admissões pode ajudar mais: admissions@meridian.edu ou "
            "+1 (555) 204-7890, de segunda a sexta, das 9h às 17h.",
            "Não posso ir além nesse ponto, e prefiro dizer isso a ficar "
            "repetindo. As admissões podem ajudar em admissions@meridian.edu "
            "ou +1 (555) 204-7890. Posso ajudar em mais alguma coisa?",
        ),
        "unoffered": (
            "A Meridian University não oferece {subject}. Posso falar sobre os "
            "programas que oferecemos ou colocar você em contato com o setor "
            "de admissões.",
            "{subject_cap} não é um dos programas da Meridian University. "
            "Terei prazer em explicar o que oferecemos, ou o setor de "
            "admissões pode ajudar.",
        ),
        "unoffered_markers": ("não oferece", "não é um dos programas"),
    },
    "zh": {
        "variants": (
            "我手头没有这个细节。我可以查询后再回复您，或者帮您了解课程的其他"
            "方面。",
            "我无法根据学校资料确认这一点。课程、学费或申请方面的其他问题，"
            "我都可以帮您。",
            "我不想猜测。我可以为您说明资料里涵盖的内容。",
        ),
        "escalations": (
            "学校资料里关于这一点只有这些。招生办可以进一步帮您："
            "admissions@meridian.edu 或 +1 (555) 204-7890，周一至周五 "
            "9:00–17:00。",
            "这一点我只能说到这里，与其反复解释，不如请您联系招生办："
            "admissions@meridian.edu 或 +1 (555) 204-7890。还有什么我可以帮您的吗？",
        ),
        "unoffered": (
            "Meridian University 不提供{subject}。我可以为您介绍我们提供的"
            "课程，或帮您联系招生办。",
            "{subject_cap}不是 Meridian University 的课程之一。我很乐意为您"
            "介绍我们提供的课程，招生办也可以进一步帮您。",
        ),
        "unoffered_markers": ("不提供", "不是 Meridian University 的课程"),
    },
}

#: Streamlit onboarding and program-flow prompts. English keeps its literals in
#: app.py; every language the selector offers has an entry here, Hindi and
#: Gujarati included, so a selected language never falls through to an English
#: prompt. The document-upload and qualification prompts are part of this table
#: now as well -- they used to be hardcoded in app.py and stayed English for
#: every language.
STREAMLIT_UI = {
    "hi": {
        "greeting": "नमस्ते! मैं आपकी विश्वविद्यालय प्रवेश सलाहकार हूँ।",
        "ask_name": " शुरू करने से पहले, क्या आप अपना नाम बता सकते हैं?",
        "skip_ok": "कोई बात नहीं — आप मुझसे कार्यक्रमों, फीस, पात्रता या आवेदन के बारे में कुछ भी पूछ सकते हैं।",
        "name_thanks_email": "धन्यवाद, {name}! और आपका ईमेल पता क्या है? मैं इसका उपयोग आपको कार्यक्रम की जानकारी भेजने के लिए करूँगी।",
        "name_too_long": "यह नाम के लिए थोड़ा लंबा है। क्या आप सिर्फ़ अपना पहला और अंतिम नाम बता सकते हैं?",
        "email_ok": "समझ गई! और आपका फ़ोन नंबर? (ताकि प्रवेश सलाहकार आपसे संपर्क कर सकें)",
        "email_bad": "यह ईमेल पता जैसा नहीं लगता। क्या आप एक मान्य ईमेल साझा कर सकते हैं? (उदाहरण: name@example.com)",
        "phone_bad": "यह फ़ोन नंबर जैसा नहीं लगता। कृपया कम से कम 7 अंकों का नंबर बताएँ, या 'skip' लिखें।",
        "profile_saved": "बहुत बढ़िया! मेरे पास आपकी जानकारी है:\n- नाम: {name}\n- ईमेल: {email}\n- फ़ोन: {phone}\n\nक्या यह सही है? ('हाँ' लिखें या मुझे बताएँ कि क्या बदलना है)",
        "applying_name": "मुझे आपको आवेदन में मदद करके खुशी होगी! पहले, क्या आप अपना नाम बता सकते हैं?",
        "program_ask": "आप किस कार्यक्रम में रुचि रखते हैं? (जैसे B.Tech Computer Science, MBA, M.Tech)",
        "program_unknown": "मैं कार्यक्रम का नाम नहीं समझ पाई। कौन सा कार्यक्रम? (जैसे B.Tech Computer Science, MBA, M.Tech)",
        "program_requirements": "**{prog}** — बहुत अच्छा चुनाव! 🎓\n\nआगे बढ़ने से पहले, आवश्यकताएँ इस प्रकार हैं:\n📋 पात्रता: {reqs}\n\nक्या आप इन आवश्यकताओं को पूरा करते हैं? (हाँ/नहीं)",
        "docs_prompt": "बहुत बढ़िया! *{prog}* में आपके प्रवेश की प्रक्रिया शुरू करने के लिए, कृपया ये दस्तावेज़ अपलोड करें:\n\n📄 **अंकसूची / मार्कशीट**\n🆔 **पहचान पत्र** (पासपोर्ट, आधार आदि)\n\nदस्तावेज़ जमा करने के लिए **नीचे** दिए गए अपलोड विजेट ⬇️ का उपयोग करें। अपलोड होने के बाद आपका प्रवेश पत्र (offer letter) स्वतः बन जाएगा!",
        "the_program": "इस कार्यक्रम",
        "qualification_no": "कोई बात नहीं! आप हमारे कार्यक्रमों के बारे में अभी भी जान सकते हैं। प्रवेश आवश्यकताओं, वैकल्पिक कार्यक्रमों या अपनी आवेदन कैसे तैयार करें, इस बारे में मुझसे पूछ सकते हैं।",
        "qualification_retry": "कृपया 'हाँ' या 'नहीं' में उत्तर दें — क्या आप इस कार्यक्रम की आवश्यकताएँ पूरी करते हैं?",
        "profile_confirmed": "आपकी प्रोफ़ाइल की पुष्टि हो गई! आपकी रुचि *{prog}* में है।\n\nक्या आप आवेदन आगे बढ़ाना चाहते हैं? बस लिखें: 'मुझे प्रवेश लेना है'",
        "program_example": "या बस लिखें: 'मुझे B.Tech Computer Science में प्रवेश लेना है'",
        "restart": "नमस्ते! मैं आपकी विश्वविद्यालय प्रवेश सलाहकार हूँ। मुझसे कार्यक्रमों, फीस, पात्रता या आवेदन के बारे में कुछ भी पूछें।",
    },
    "gu": {
        "greeting": "નમસ્તે! હું તમારી યુનિવર્સિટી એડમિશન સલાહકાર છું.",
        "ask_name": " શરૂ કરતા પહેલા, શું તમે તમારું નામ કહી શકશો?",
        "skip_ok": "કોઈ વાંધો નહીં — તમે મને કાર્યક્રમો, ફી, પાત્રતા અથવા અરજી વિશે કંઈ પણ પૂછી શકો છો.",
        "name_thanks_email": "આભાર, {name}! અને તમારું ઈમેલ સરનામું શું છે? હું તેનો ઉપયોગ તમને કાર્યક્રમની વિગતો મોકલવા માટે કરીશ.",
        "name_too_long": "આ નામ માટે થોડું લાંબું છે. શું તમે ફક્ત તમારું પહેલું અને છેલ્લું નામ કહી શકશો?",
        "email_ok": "સમજી ગઈ! અને તમારો ફોન નંબર? (જેથી એડમિશન સલાહકાર તમારો સંપર્ક કરી શકે)",
        "email_bad": "આ ઈમેલ સરનામા જેવું લાગતું નથી. શું તમે માન્ય ઈમેલ આપી શકશો? (ઉદાહરણ: name@example.com)",
        "phone_bad": "આ ફોન નંબર જેવું લાગતું નથી. કૃપા કરીને ઓછામાં ઓછા 7 અંકોનો નંબર આપો, અથવા 'skip' લખો.",
        "profile_saved": "ખૂબ સરસ! મારી પાસે તમારી માહિતી છે:\n- નામ: {name}\n- ઈમેલ: {email}\n- ફોન: {phone}\n\nશું આ સાચું છે? ('હા' લખો અથવા મને કહો કે શું બદલવું છે)",
        "applying_name": "મને તમને અરજીમાં મદદ કરવામાં આનંદ થશે! પહેલા, શું તમે તમારું નામ કહી શકશો?",
        "program_ask": "તમને કયો કાર્યક્રમ રસ છે? (જેમ કે B.Tech Computer Science, MBA, M.Tech)",
        "program_unknown": "મને કાર્યક્રમનું નામ સમજાયું નહીં. કયો કાર્યક્રમ? (જેમ કે B.Tech Computer Science, MBA, M.Tech)",
        "program_requirements": "**{prog}** — ખૂબ સરસ પસંદગી! 🎓\n\nઆગળ વધતા પહેલા, આવશ્યકતાઓ આ પ્રમાણે છે:\n📋 પાત્રતા: {reqs}\n\nશું તમે આ આવશ્યકતાઓ પૂરી કરો છો? (હા/ના)",
        "docs_prompt": "ખૂબ સરસ! *{prog}* માટે તમારી પ્રવેશ પ્રક્રિયા શરૂ કરવા, કૃપા કરીને આ દસ્તાવેજો અપલોડ કરો:\n\n📄 **માર્કશીટ / અંકપત્રક**\n🆔 **ઓળખ પત્ર** (પાસપોર્ટ, આધાર વગેરે)\n\nદસ્તાવેજો સબમિટ કરવા માટે **નીચે** આપેલા અપલોડ વિજેટ ⬇️ નો ઉપયોગ કરો. અપલોડ થયા પછી તમારો ઓફર લેટર આપમેળે બની જશે!",
        "the_program": "આ કાર્યક્રમ",
        "qualification_no": "કોઈ વાંધો નહીં! તમે હજી પણ અમારા કાર્યક્રમો વિશે જાણી શકો છો. પ્રવેશ આવશ્યકતાઓ, વૈકલ્પિક કાર્યક્રમો અથવા તમારી અરજી કેવી રીતે તૈયાર કરવી તે વિશે મને પૂછો.",
        "qualification_retry": "કૃપા કરીને 'હા' અથવા 'ના' માં જવાબ આપો — શું તમે આ કાર્યક્રમની આવશ્યકતાઓ પૂરી કરો છો?",
        "profile_confirmed": "તમારી પ્રોફાઇલની પુષ્ટિ થઈ ગઈ! તમને *{prog}*માં રસ છે.\n\nશું તમે અરજી આગળ વધારવા માંગો છો? ફક્ત લખો: 'મને પ્રવેશ લેવો છે'",
        "program_example": "અથવા ફક્ત લખો: 'મને B.Tech Computer Science માં પ્રવેશ લેવો છે'",
        "restart": "નમસ્તે! હું તમારી યુનિવર્સિટી એડમિશન સલાહકાર છું. મને કાર્યક્રમો, ફી, પાત્રતા અથવા અરજી વિશે કંઈ પણ પૂછો.",
    },
    "es": {
        "greeting": "¡Hola! Soy su asesora de admisiones universitarias.",
        "ask_name": " Antes de empezar, ¿me dice su nombre?",
        "skip_ok": "No hay problema: pregúnteme lo que quiera sobre programas, tasas, requisitos o solicitudes.",
        "name_thanks_email": "¡Gracias, {name}! ¿Y cuál es su correo electrónico? Lo usaré para enviarle información sobre los programas.",
        "name_too_long": "Eso es un poco largo para un nombre. ¿Me dice solo su nombre y apellido?",
        "email_ok": "¡Perfecto! ¿Y su número de teléfono? (Para que un asesor de admisiones pueda contactarle)",
        "email_bad": "Eso no parece una dirección de correo válida. ¿Puede compartir un correo válido? (por ejemplo, nombre@ejemplo.com)",
        "phone_bad": "Eso no parece un número de teléfono. Comparta un número con al menos 7 dígitos o escriba «skip».",
        "profile_saved": "¡Perfecto! Tengo sus datos:\n- Nombre: {name}\n- Correo: {email}\n- Teléfono: {phone}\n\n¿Es correcto? (Escriba «sí» o dígame qué cambiar)",
        "applying_name": "¡Con gusto le ayudo a solicitar admisión! Primero, ¿me dice su nombre?",
        "program_ask": "¿Qué programa le interesa? (por ejemplo, B.Tech Computer Science, MBA, M.Tech)",
        "program_unknown": "No entendí el nombre del programa. ¿Cuál es? (por ejemplo, B.Tech Computer Science, MBA, M.Tech)",
        "program_requirements": "**{prog}** — ¡excelente elección! 🎓\n\nAntes de continuar, estos son los requisitos:\n📋 Requisito: {reqs}\n\n¿Cumple estos requisitos? (sí/no)",
        "docs_prompt": "¡Excelente! Para procesar su admisión en *{prog}*, suba los siguientes documentos:\n\n📄 **Expediente académico / boletín de notas**\n🆔 **Documento de identidad** (pasaporte, Aadhaar, etc.)\n\nUse el widget de carga **de abajo** ⬇️ para enviar sus documentos. Cuando termine, su carta de admisión se generará automáticamente.",
        "the_program": "este programa",
        "qualification_no": "¡No se preocupe! Puede seguir explorando nuestros programas. Pregúnteme por los requisitos de admisión, programas alternativos o cómo preparar su solicitud.",
        "qualification_retry": "Responda «sí» o «no»: ¿cumple los requisitos de este programa?",
        "profile_confirmed": "¡Su perfil está confirmado! Le interesa *{prog}*.\n\n¿Desea continuar con la solicitud? Solo diga: «Quiero tomar la admisión»",
        "program_example": "O simplemente diga: «Quiero tomar la admisión en B.Tech Computer Science»",
        "restart": "¡Hola! Soy su asesora de admisiones universitarias. Pregúnteme lo que quiera sobre programas, tasas, requisitos o solicitudes.",
    },
    "fr": {
        "greeting": "Bonjour ! Je suis votre conseillère en admissions universitaires.",
        "ask_name": " Avant de commencer, pourriez-vous me dire votre nom ?",
        "skip_ok": "Pas de problème — posez-moi vos questions sur les programmes, les frais, l'éligibilité ou les candidatures.",
        "name_thanks_email": "Merci {name} ! Et quelle est votre adresse e-mail ? Elle me servira à vous envoyer les informations sur les programmes.",
        "name_too_long": "C'est un peu long pour un nom. Pourriez-vous me donner simplement votre prénom et votre nom ?",
        "email_ok": "Parfait ! Et votre numéro de téléphone ? (Pour qu'un conseiller en admissions puisse vous recontacter)",
        "email_bad": "Cela ne ressemble pas à une adresse e-mail valide. Pourriez-vous en donner une valide ? (par exemple, nom@exemple.com)",
        "phone_bad": "Cela ne ressemble pas à un numéro de téléphone. Indiquez un numéro d'au moins 7 chiffres ou tapez « skip ».",
        "profile_saved": "Parfait ! Voici vos informations :\n- Nom : {name}\n- E-mail : {email}\n- Téléphone : {phone}\n\nEst-ce correct ? (Tapez « oui » ou dites-moi quoi corriger)",
        "applying_name": "Je serais ravie de vous aider à candidater ! D'abord, pourriez-vous me dire votre nom ?",
        "program_ask": "Quel programme vous intéresse ? (par exemple, B.Tech Computer Science, MBA, M.Tech)",
        "program_unknown": "Je n'ai pas saisi le nom du programme. Lequel ? (par exemple, B.Tech Computer Science, MBA, M.Tech)",
        "program_requirements": "**{prog}** — excellent choix ! 🎓\n\nAvant de continuer, voici les conditions :\n📋 Condition : {reqs}\n\nRemplissez-vous ces conditions ? (oui/non)",
        "docs_prompt": "Parfait ! Pour traiter votre admission en *{prog}*, veuillez téléverser :\n\n📄 **Relevé de notes / bulletin**\n🆔 **Pièce d'identité** (passeport, Aadhaar, etc.)\n\nUtilisez le widget de téléversement **ci-dessous** ⬇️ pour envoyer vos documents. Une fois téléversés, votre lettre d'admission sera générée automatiquement.",
        "the_program": "ce programme",
        "qualification_no": "Pas de souci ! Vous pouvez continuer à explorer nos programmes. Demandez-moi les conditions d'admission, les programmes alternatifs ou comment préparer votre dossier.",
        "qualification_retry": "Répondez « oui » ou « non » : remplissez-vous les conditions de ce programme ?",
        "profile_confirmed": "Votre profil est confirmé ! Vous êtes intéressé(e) par *{prog}*.\n\nSouhaitez-vous poursuivre la candidature ? Dites simplement : « Je veux prendre l'admission »",
        "program_example": "Ou dites simplement : « Je veux prendre l'admission en B.Tech Computer Science »",
        "restart": "Bonjour ! Je suis votre conseillère en admissions universitaires. Posez-moi toutes vos questions sur les programmes, les frais, les conditions ou les candidatures.",
    },
    "it": {
        "greeting": "Ciao! Sono la sua consulente per le ammissioni universitarie.",
        "ask_name": " Prima di iniziare, mi dice il suo nome?",
        "skip_ok": "Nessun problema: mi chieda pure informazioni su programmi, tasse, requisiti o iscrizioni.",
        "name_thanks_email": "Grazie {name}! E qual è il suo indirizzo e-mail? Lo userò per inviarle i dettagli dei programmi.",
        "name_too_long": "È un po' lungo per un nome. Può dirmi solo nome e cognome?",
        "email_ok": "Perfetto! E il suo numero di telefono? (Così un consulente per le ammissioni può ricontattarla)",
        "email_bad": "Non sembra un indirizzo e-mail valido. Può condividerne uno valido? (ad esempio, nome@esempio.com)",
        "phone_bad": "Non sembra un numero di telefono. Inserisca un numero con almeno 7 cifre o scriva «skip».",
        "profile_saved": "Perfetto! Ho i suoi dati:\n- Nome: {name}\n- E-mail: {email}\n- Telefono: {phone}\n\nÈ corretto? (Scriva «sì» o mi dica cosa cambiare)",
        "applying_name": "Sarò lieta di aiutarla a iscriversi! Prima, mi dice il suo nome?",
        "program_ask": "Quale programma le interessa? (ad esempio, B.Tech Computer Science, MBA, M.Tech)",
        "program_unknown": "Non ho capito il nome del programma. Quale? (ad esempio, B.Tech Computer Science, MBA, M.Tech)",
        "program_requirements": "**{prog}** — ottima scelta! 🎓\n\nPrima di procedere, ecco i requisiti:\n📋 Requisito: {reqs}\n\nSoddisfa questi requisiti? (sì/no)",
        "docs_prompt": "Perfetto! Per elaborare la sua ammissione a *{prog}*, carichi:\n\n📄 **Pagella / certificato dei voti**\n🆔 **Documento d'identità** (passaporto, Aadhaar, ecc.)\n\nUsi il widget di caricamento **qui sotto** ⬇️ per inviare i documenti. Una volta caricati, la lettera di ammissione verrà generata automaticamente.",
        "the_program": "questo programma",
        "qualification_no": "Nessun problema! Può comunque esplorare i nostri programmi. Mi chieda dei requisiti di ammissione, dei programmi alternativi o di come preparare la domanda.",
        "qualification_retry": "Risponda «sì» o «no»: soddisfa i requisiti di questo programma?",
        "profile_confirmed": "Il suo profilo è confermato! È interessato a *{prog}*.\n\nVuole procedere con la domanda? Dica semplicemente: «Voglio prendere l'ammissione»",
        "program_example": "Oppure dica semplicemente: «Voglio prendere l'ammissione in B.Tech Computer Science»",
        "restart": "Ciao! Sono la sua consulente per le ammissioni universitarie. Mi chieda pure informazioni su programmi, tasse, requisiti o iscrizioni.",
    },
    "ja": {
        "greeting": "こんにちは。大学入学アドバイザーです。",
        "ask_name": " まず、お名前を教えていただけますか？",
        "skip_ok": "問題ありません。プログラム、学費、出願資格、申請方法について何でもお尋ねください。",
        "name_thanks_email": "{name}さん、ありがとうございます。メールアドレスを教えていただけますか？プログラムの詳細をお送りします。",
        "name_too_long": "お名前としては少し長いようです。姓と名だけを教えていただけますか？",
        "email_ok": "ありがとうございます。電話番号も教えていただけますか？（入学アドバイザーがご連絡できるように）",
        "email_bad": "有効なメールアドレスのようには見えません。有効なアドレスを教えていただけますか？（例：name@example.com）",
        "phone_bad": "電話番号のようには見えません。7桁以上の番号を教えていただくか、「skip」と入力してください。",
        "profile_saved": "ありがとうございます。お客様の情報は以下のとおりです。\n- お名前: {name}\n- メール: {email}\n- 電話: {phone}\n\nこれでよろしいですか？（「はい」と入力するか、修正内容をお知らせください）",
        "applying_name": "出願のお手伝いをいたします。まず、お名前を教えていただけますか？",
        "program_ask": "ご興味のあるプログラムはどれですか？（例：B.Tech Computer Science、MBA、M.Tech）",
        "program_unknown": "プログラム名を聞き取れませんでした。どれですか？（例：B.Tech Computer Science、MBA、M.Tech）",
        "program_requirements": "**{prog}** — すばらしい選択です！🎓\n\n進む前に、要件は次のとおりです。\n📋 要件：{reqs}\n\nこの要件を満たしていますか？（はい／いいえ）",
        "docs_prompt": "ありがとうございます。*{prog}* の入学手続きを進めるため、以下をアップロードしてください。\n\n📄 **成績証明書**\n🆔 **身分証明書**（パスポート、Aadhaar など）\n\n**下の**アップロード欄 ⬇️ から書類を送信してください。アップロードが完了すると、入学許可証が自動的に作成されます。",
        "the_program": "このプログラム",
        "qualification_no": "ご安心ください。ほかのプログラムについてもご案内できます。入学要件、別のプログラム、出願の準備方法など、何でもお尋ねください。",
        "qualification_retry": "「はい」または「いいえ」でお答えください。このプログラムの要件を満たしていますか？",
        "profile_confirmed": "プロフィールを確認しました。ご希望は *{prog}* ですね。\n\n出願を進めますか？「入学を申し込みたい」とお伝えください。",
        "program_example": "または「B.Tech Computer Science に入学を申し込みたい」とお伝えください。",
        "restart": "こんにちは。大学入学アドバイザーです。プログラム、学費、出願資格、申請方法について何でもお尋ねください。",
    },
    "pt": {
        "greeting": "Olá! Sou sua consultora de admissões universitárias.",
        "ask_name": " Antes de começar, pode me dizer seu nome?",
        "skip_ok": "Sem problema — pergunte o que quiser sobre programas, mensalidades, requisitos ou inscrições.",
        "name_thanks_email": "Obrigada, {name}! E qual é o seu e-mail? Vou usá-lo para enviar informações sobre os programas.",
        "name_too_long": "Isso é um pouco longo para um nome. Pode me dizer apenas seu nome e sobrenome?",
        "email_ok": "Perfeito! E o seu telefone? (Para que um consultor de admissões possa entrar em contato)",
        "email_bad": "Isso não parece um endereço de e-mail válido. Pode compartilhar um válido? (por exemplo, nome@exemplo.com)",
        "phone_bad": "Isso não parece um número de telefone. Informe um número com pelo menos 7 dígitos ou digite «skip».",
        "profile_saved": "Perfeito! Tenho suas informações:\n- Nome: {name}\n- E-mail: {email}\n- Telefone: {phone}\n\nEstá correto? (Digite «sim» ou me diga o que corrigir)",
        "applying_name": "Terei prazer em ajudar você a se inscrever! Primeiro, pode me dizer seu nome?",
        "program_ask": "Qual programa você quer? (por exemplo, B.Tech Computer Science, MBA, M.Tech)",
        "program_unknown": "Não entendi o nome do programa. Qual é? (por exemplo, B.Tech Computer Science, MBA, M.Tech)",
        "program_requirements": "**{prog}** — ótima escolha! 🎓\n\nAntes de continuar, veja os requisitos:\n📋 Requisito: {reqs}\n\nVocê atende a estes requisitos? (sim/não)",
        "docs_prompt": "Excelente! Para processar sua admissão em *{prog}*, envie:\n\n📄 **Histórico escolar / boletim**\n🆔 **Documento de identidade** (passaporte, Aadhaar, etc.)\n\nUse o widget de upload **abaixo** ⬇️ para enviar os documentos. Assim que forem enviados, sua carta de admissão será gerada automaticamente.",
        "the_program": "este programa",
        "qualification_no": "Sem problema! Você ainda pode conhecer nossos programas. Pergunte sobre requisitos de admissão, programas alternativos ou como preparar sua inscrição.",
        "qualification_retry": "Responda «sim» ou «não»: você atende aos requisitos deste programa?",
        "profile_confirmed": "Seu perfil está confirmado! Você tem interesse em *{prog}*.\n\nDeseja continuar com a inscrição? Basta dizer: «Quero me inscrever»",
        "program_example": "Ou basta dizer: «Quero me inscrever em B.Tech Computer Science»",
        "restart": "Olá! Sou sua consultora de admissões universitárias. Pergunte o que quiser sobre programas, mensalidades, requisitos ou inscrições.",
    },
    "zh": {
        "greeting": "您好！我是您的大学招生顾问。",
        "ask_name": " 在开始之前，请告诉我您的姓名好吗？",
        "skip_ok": "没关系，您可以随时问我课程、学费、申请资格或申请流程方面的问题。",
        "name_thanks_email": "谢谢您，{name}！请问您的电子邮箱是多少？我会用它给您发送课程详情。",
        "name_too_long": "这作为姓名有点长。请只告诉我您的姓和名，好吗？",
        "email_ok": "好的！请问您的电话号码是多少？（方便招生顾问跟进联系）",
        "email_bad": "这看起来不像有效的电子邮箱地址。请提供一个有效的邮箱（例如 name@example.com）。",
        "phone_bad": "这看起来不像电话号码。请提供至少 7 位数字的号码，或输入「skip」。",
        "profile_saved": "好的！您的信息如下：\n- 姓名：{name}\n- 邮箱：{email}\n- 电话：{phone}\n\n这些信息正确吗？（输入「是」或告诉我需要修改什么）",
        "applying_name": "我很乐意帮您申请！首先，请告诉我您的姓名好吗？",
        "program_ask": "您对哪个课程感兴趣？（例如：B.Tech Computer Science、MBA、M.Tech）",
        "program_unknown": "我没听清课程名称。请问是哪个课程？（例如：B.Tech Computer Science、MBA、M.Tech）",
        "program_requirements": "**{prog}** — 很好的选择！🎓\n\n在继续之前，以下是申请要求：\n📋 要求：{reqs}\n\n您符合这些要求吗？（是／否）",
        "docs_prompt": "太好了！为了处理您报读 *{prog}* 的入学申请，请上传：\n\n📄 **成绩单**\n🆔 **身份证明**（护照、Aadhaar 等）\n\n请使用**下方**的上传控件 ⬇️ 提交文件。上传后，您的录取通知书将自动生成。",
        "the_program": "该课程",
        "qualification_no": "没关系！您仍然可以了解我们的课程。欢迎询问入学要求、其他课程或如何准备申请。",
        "qualification_retry": "请回答「是」或「否」：您符合这个课程的要求吗？",
        "profile_confirmed": "您的资料已确认！您对 *{prog}* 感兴趣。\n\n您要继续申请吗？只需说：「我想申请入学」",
        "program_example": "或者直接说：「我想申请 B.Tech Computer Science 课程」",
        "restart": "您好！我是您的大学招生顾问。您可以随时问我课程、学费、申请资格或申请流程方面的问题。",
    },
}

#: Program eligibility phrases, keyed by the English string in
#: ``app.PROGRAM_ELIGIBILITY``. A requirement that is not listed falls back to
#: the English phrase, so adding a program never breaks the prompt.
ELIGIBILITY_TEXT = {
    "hi": {
        "a bachelor's degree": "स्नातक की डिग्री",
        "a B.Tech or B.E. degree": "B.Tech या B.E. की डिग्री",
        "a bachelor's degree in computing": "कंप्यूटिंग में स्नातक की डिग्री",
        "a bachelor's degree in commerce": "वाणिज्य में स्नातक की डिग्री",
        "a bachelor's degree in science": "विज्ञान में स्नातक की डिग्री",
        "10+2 with Physics, Chemistry and Mathematics (PCM)": "भौतिकी, रसायन और गणित के साथ 10+2 (PCM)",
        "10+2 with PCM or PCB": "PCM या PCB के साथ 10+2",
        "10+2 in any stream": "किसी भी संकाय में 10+2",
        "a relevant bachelor's degree": "प्रासंगिक स्नातक की डिग्री",
    },
    "gu": {
        "a bachelor's degree": "સ્નાતકની ડિગ્રી",
        "a B.Tech or B.E. degree": "B.Tech અથવા B.E.ની ડિગ્રી",
        "a bachelor's degree in computing": "કમ્પ્યુટિંગમાં સ્નાતકની ડિગ્રી",
        "a bachelor's degree in commerce": "વાણિજ્યમાં સ્નાતકની ડિગ્રી",
        "a bachelor's degree in science": "વિજ્ઞાનમાં સ્નાતકની ડિગ્રી",
        "10+2 with Physics, Chemistry and Mathematics (PCM)": "ભૌતિકશાસ્ત્ર, રસાયણશાસ્ત્ર અને ગણિત સાથે 10+2 (PCM)",
        "10+2 with PCM or PCB": "PCM અથવા PCB સાથે 10+2",
        "10+2 in any stream": "કોઈપણ સ્ટ્રીમમાં 10+2",
        "a relevant bachelor's degree": "સંબંધિત સ્નાતકની ડિગ્રી",
    },
    "es": {
        "a bachelor's degree": "una licenciatura (título de grado)",
        "a B.Tech or B.E. degree": "un título de B.Tech o B.E.",
        "a bachelor's degree in computing": "una licenciatura en informática",
        "a bachelor's degree in commerce": "una licenciatura en comercio",
        "a bachelor's degree in science": "una licenciatura en ciencias",
        "10+2 with Physics, Chemistry and Mathematics (PCM)": "10+2 con Física, Química y Matemáticas (PCM)",
        "10+2 with PCM or PCB": "10+2 con PCM o PCB",
        "10+2 in any stream": "10+2 en cualquier especialidad",
        "a relevant bachelor's degree": "una licenciatura relevante",
    },
    "fr": {
        "a bachelor's degree": "un diplôme de licence",
        "a B.Tech or B.E. degree": "un diplôme de B.Tech ou B.E.",
        "a bachelor's degree in computing": "une licence en informatique",
        "a bachelor's degree in commerce": "une licence en commerce",
        "a bachelor's degree in science": "une licence en sciences",
        "10+2 with Physics, Chemistry and Mathematics (PCM)": "10+2 avec physique, chimie et mathématiques (PCM)",
        "10+2 with PCM or PCB": "10+2 avec PCM ou PCB",
        "10+2 in any stream": "10+2, toutes filières confondues",
        "a relevant bachelor's degree": "une licence pertinente",
    },
    "it": {
        "a bachelor's degree": "una laurea triennale",
        "a B.Tech or B.E. degree": "una laurea in B.Tech o B.E.",
        "a bachelor's degree in computing": "una laurea in informatica",
        "a bachelor's degree in commerce": "una laurea in commercio",
        "a bachelor's degree in science": "una laurea in scienze",
        "10+2 with Physics, Chemistry and Mathematics (PCM)": "10+2 con fisica, chimica e matematica (PCM)",
        "10+2 with PCM or PCB": "10+2 con PCM o PCB",
        "10+2 in any stream": "10+2 in qualsiasi indirizzo",
        "a relevant bachelor's degree": "una laurea pertinente",
    },
    "ja": {
        "a bachelor's degree": "学士号",
        "a B.Tech or B.E. degree": "B.Tech または B.E. の学位",
        "a bachelor's degree in computing": "コンピューティング分野の学士号",
        "a bachelor's degree in commerce": "商学の学士号",
        "a bachelor's degree in science": "理学の学士号",
        "10+2 with Physics, Chemistry and Mathematics (PCM)": "物理・化学・数学を含む 10+2（PCM）",
        "10+2 with PCM or PCB": "PCM または PCB を含む 10+2",
        "10+2 in any stream": "任意の専攻で 10+2",
        "a relevant bachelor's degree": "関連分野の学士号",
    },
    "pt": {
        "a bachelor's degree": "um bacharelado",
        "a B.Tech or B.E. degree": "um diploma de B.Tech ou B.E.",
        "a bachelor's degree in computing": "um bacharelado em computação",
        "a bachelor's degree in commerce": "um bacharelado em comércio",
        "a bachelor's degree in science": "um bacharelado em ciências",
        "10+2 with Physics, Chemistry and Mathematics (PCM)": "10+2 com Física, Química e Matemática (PCM)",
        "10+2 with PCM or PCB": "10+2 com PCM ou PCB",
        "10+2 in any stream": "10+2 em qualquer área",
        "a relevant bachelor's degree": "um bacharelado relevante",
    },
    "zh": {
        "a bachelor's degree": "本科学位",
        "a B.Tech or B.E. degree": "B.Tech 或 B.E. 学位",
        "a bachelor's degree in computing": "计算机相关本科学位",
        "a bachelor's degree in commerce": "商科本科学位",
        "a bachelor's degree in science": "理学本科学位",
        "10+2 with Physics, Chemistry and Mathematics (PCM)": "10+2，包含物理、化学和数学（PCM）",
        "10+2 with PCM or PCB": "10+2，包含 PCM 或 PCB",
        "10+2 in any stream": "任意方向的 10+2",
        "a relevant bachelor's degree": "相关领域的本科学位",
    },
}


def eligibility_text(requirement: str, language: str) -> str:
    """The eligibility phrase in *language* (English when untranslated)."""
    code = normalize_language_code(language)
    return ELIGIBILITY_TEXT.get(code, {}).get(requirement, requirement)


def streamlit_ui(key: str, language: str, default: str, **kwargs) -> str:
    """A Streamlit prompt in *language*, or the caller's English literal.

    Pure on purpose: ``app.py`` starts a Streamlit run when imported, so the
    prompt table is what the tests can reach. English has no entry and gets
    *default* unchanged, which is how its literals stay in ``app.py``.
    """
    template = (STREAMLIT_UI.get(normalize_language_code(language)) or {}).get(
        key, default
    )
    return template.format(**kwargs) if kwargs else template


#: English literals for the opening message. They stay here so ``app.py`` has
#: one greeting function rather than two copies that can drift.
GREETING_DEFAULT = "Hello! I'm your University Admissions Advisor."
ASK_NAME_DEFAULT = " Before we start, could you tell me your name?"


def greeting_text(language: str, lead_collected: bool = False) -> str:
    """The opening message, with the name prompt when the profile is empty.

    Called when the chat is created and again when the student changes the
    language before saying anything — the second call is what stops a Hindi
    chat from opening with an English question.
    """
    text = streamlit_ui("greeting", language, GREETING_DEFAULT)
    if not lead_collected:
        text += streamlit_ui("ask_name", language, ASK_NAME_DEFAULT)
    return text


#: Sent for ``/language`` or ``/languages``.
WHATSAPP_LANGUAGE_HELP = (
    "You can choose a language with any of these commands:\n"
    "• English or /en\n"
    "• Hindi or /hi\n"
    "• Gujarati or /gu\n"
    "• Spanish or /es\n"
    "• French or /fr\n"
    "• Italian or /it\n"
    "• Japanese or /ja\n"
    "• Portuguese or /pt\n"
    "• Chinese or /zh"
)

WHATSAPP_LANGUAGE_MENU_HEADER = (
    "Please choose the language you'd like to continue in:"
)
WHATSAPP_LANGUAGE_MENU_FOOTER = (
    "Reply with the number or the language name. "
    "You can change this later with /language."
)


def whatsapp_language_menu(options) -> str:
    """The numbered first-contact menu for the given option tuples.

    ``options`` is ``[(digit, code, label), ...]`` from
    ``app.languages.whatsapp_language_menu_options``. English is the menu
    language because no language has been chosen yet; the labels themselves
    are native so the choice is readable without it.
    """
    lines = [WHATSAPP_LANGUAGE_MENU_HEADER, ""]
    lines.extend(f"{digit}. {label}" for digit, _code, label in options)
    lines.extend(["", WHATSAPP_LANGUAGE_MENU_FOOTER])
    return "\n".join(lines)
