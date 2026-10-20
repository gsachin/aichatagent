"""Localized deterministic WhatsApp chat strings.

English literals stay at their call sites in ``app/main.py`` (the same pattern
as ``app.localized_text.streamlit_ui``). This table holds the eight selectable
languages; an absent key/language returns the caller's English default
unchanged, so a missing translation can never break the webhook.

The helper only selects/formats text. It never escapes, strips Markdown or
translates interpolated values: names, emails, programs and the final TwiML
boundary keep their existing handling.

The ASCII instruction keywords (``done``, ``accept``, ``decline`` and
``I want to take admission``) are deliberately kept in English inside every
translation because the deterministic state machine matches them in English.
"""
from __future__ import annotations

import logging

from app.languages import normalize_language_code

logger = logging.getLogger("whatsapp_text")

WHATSAPP_TEXT_LANGUAGES = ("hi", "gu", "es", "fr", "it", "ja", "pt", "zh")

WHATSAPP_TEXT_KEYS = (
    "media_audio_unsupported",
    "media_document_received",
    "media_unknown_type",
    "empty_message",
    "greeting_known_name",
    "greeting_unknown_name",
    "name_corrected_ask_email",
    "name_corrected_help",
    "name_change_prompt",
    "name_saved_help",
    "name_not_caught",
    "update_name_prompt",
    "name_captured_ask_email",
    "name_captured_profile_updated",
    "ask_name_missing",
    "email_saved_with_name",
    "email_saved_no_name",
    "update_email_saved",
    "update_email_prompt",
    "email_captured_ask_name",
    "email_captured_help",
    "email_missing_ask",
    "program_detected",
    "program_choice",
    "program_ask_awaiting",
    "program_ask_interested",
    "program_ask_update",
    "admission_docs_prompt",
    "update_field_prompt",
    "update_field_unknown_choice",
    "offer_accepted",
    "offer_declined",
    "offer_not_found",
    "yes_confirmed_program",
    "offer_missing_fields",
    "offer_no_documents",
    "offer_success",
    "offer_failure",
    "rag_failure",
    "safety_fallback",
)


WHATSAPP_TEXT: dict[str, dict[str, str]] = {
    "hi": {
        "media_audio_unsupported": (
            "मैं वॉइस नोट सुन नहीं सकती। कृपया अपना सवाल यहाँ चैट में टाइप "
            "करें, मैं तुरंत जवाब दे दूँगी!"
        ),
        "media_document_received": (
            "📄 आपका दस्तावेज़ मिल गया! मैं इसे प्रोसेस कर रही हूँ। अगर और "
            "दस्तावेज़ हैं तो वे भी भेज दीजिए, या पूरा होने पर 'done' टाइप करें।"
        ),
        "media_unknown_type": (
            "मुझे आपकी फ़ाइल मिली, लेकिन मैं केवल फ़ोटो (ट्रांसक्रिप्ट, आईडी) "
            "और PDF स्वीकार कर सकती हूँ। कृपया इसे इमेज या PDF के रूप में भेजें।"
        ),
        "empty_message": (
            "नमस्ते! मेरिडियन यूनिवर्सिटी एडमिशन के बारे में अपना सवाल भेजिए।"
        ),
        "greeting_known_name": (
            "नमस्ते {name}! 👋 मैं मेरिडियन यूनिवर्सिटी की एडमिशन सहायिका हूँ। "
            "आप क्या जानना चाहेंगे?"
        ),
        "greeting_unknown_name": (
            "नमस्ते! 👋 मैं मेरिडियन यूनिवर्सिटी की एडमिशन सहायिका हूँ। "
            "आपका नाम क्या है?"
        ),
        "name_corrected_ask_email": (
            "धन्यवाद — मैंने आपका नाम {name} कर दिया है। आपका ईमेल पता क्या है?"
        ),
        "name_corrected_help": (
            "धन्यवाद — मैंने आपका नाम {name} कर दिया है। एडमिशन में मैं आपकी "
            "क्या मदद कर सकती हूँ?"
        ),
        "name_change_prompt": (
            "बिल्कुल — आप अपना नाम क्या रखना चाहेंगे? (आप सिर्फ़ नाम लिखकर "
            "भेज सकते हैं।)"
        ),
        "name_saved_help": (
            "धन्यवाद — अब आपका नाम {name} है। एडमिशन में मैं आपकी क्या मदद कर "
            "सकती हूँ?"
        ),
        "name_not_caught": (
            "माफ़ कीजिए, मुझे नाम समझ नहीं आया। इसे क्या बदलना है?"
        ),
        "update_name_prompt": "ज़रूर — आप अपना नाम क्या बदलना चाहेंगे?",
        "name_captured_ask_email": (
            "धन्यवाद {name}! आपका ईमेल पता क्या है? मैं इसका उपयोग आपको "
            "कार्यक्रम की जानकारी भेजने और आगे संपर्क करने के लिए करूँगी।"
        ),
        "name_captured_profile_updated": (
            "धन्यवाद {name}! मैंने आपकी प्रोफ़ाइल अपडेट कर दी है। मेरिडियन "
            "एडमिशन में मैं आपकी क्या मदद कर सकती हूँ?"
        ),
        "ask_name_missing": (
            "नमस्ते! मदद करने से पहले, क्या आप अपना नाम बता सकते हैं?"
        ),
        "email_saved_with_name": "धन्यवाद {name}! मैंने आपका ईमेल सहेज लिया है।",
        "email_saved_no_name": "धन्यवाद — मैंने आपका ईमेल सहेज लिया है।",
        "update_email_saved": "धन्यवाद — मैंने आपका ईमेल पता अपडेट कर दिया है।",
        "update_email_prompt": "ज़रूर — सही ईमेल पता क्या है?",
        "email_captured_ask_name": "आपका ईमेल मिल गया! और आपका नाम क्या है?",
        "email_captured_help": (
            "धन्यवाद! मैंने आपका ईमेल सहेज लिया है। आज एडमिशन में मैं आपकी "
            "क्या मदद कर सकती हूँ?"
        ),
        "email_missing_ask": (
            "नमस्ते {name}! क्या आप अपना ईमेल पता साझा कर सकते हैं? मैं इसका "
            "उपयोग आपको कार्यक्रम की जानकारी भेजने और बाद में संपर्क करने के "
            "लिए करूँगी।"
        ),
        "program_detected": (
            "**{program}** — समझ गई! 🎓\n\nएडमिशन आगे बढ़ाने के लिए कहिए: "
            "'I want to take admission' और मैं आपको दस्तावेज़ अपलोड की प्रक्रिया "
            "बताऊँगी।"
        ),
        "program_choice": (
            "**{program}** — बढ़िया चयन! 🎓\n\nएडमिशन आगे बढ़ाने के लिए कहिए: "
            "'I want to take admission' और मैं आपको दस्तावेज़ अपलोड की प्रक्रिया "
            "बताऊँगी।"
        ),
        "program_ask_awaiting": (
            "आप कौन-सा कार्यक्रम चाहेंगे? (जैसे B.Tech Computer Science, MBA, "
            "M.Tech, BCA)"
        ),
        "program_ask_interested": (
            "आप किस कार्यक्रम में रुचि रखते हैं? (जैसे B.Tech Computer Science, "
            "MBA, M.Tech, BCA)"
        ),
        "program_ask_update": (
            "ज़रूर — आप इसके बजाय कौन-सा कार्यक्रम चाहेंगे? (जैसे B.Tech "
            "Computer Science, MBA, M.Tech, BCA)"
        ),
        "admission_docs_prompt": (
            "बढ़िया! *{program}* में आपके एडमिशन के लिए कृपया ये दस्तावेज़ "
            "अपलोड करें:\n\n📄 ट्रांसक्रिप्ट / मार्कशीट\n🆔 आईडी प्रमाण "
            "(पासपोर्ट, आधार या ड्राइविंग लाइसेंस)\n📝 कोई भी अतिरिक्त "
            "प्रमाणपत्र (वैकल्पिक)\n\nसाफ़ फ़ोटो या PDF सीधे यहाँ WhatsApp पर "
            "भेजें। सब भेजने के बाद 'done' टाइप करें।"
        ),
        "update_field_prompt": (
            "कोई बात नहीं! आप क्या अपडेट करना चाहेंगे? अपना नाम, ईमेल या "
            "कार्यक्रम की रुचि?"
        ),
        "update_field_unknown_choice": (
            "आप अपना नाम, ईमेल या कार्यक्रम की रुचि अपडेट कर सकते हैं — कौन-सा?"
        ),
        "offer_accepted": (
            "🎉 बधाई हो {name}! *{program}* के लिए आपका ऑफ़र स्वीकार हो गया है। "
            "अपनी सीट पक्की करने के लिए आपको जल्द ही भुगतान लिंक मिलेगा।"
        ),
        "offer_declined": (
            "समझ गई, {name}। *{program}* के लिए आपका ऑफ़र अस्वीकार कर दिया गया "
            "है। अगर आप अपना मन बदलें या दूसरे कार्यक्रम देखना चाहें, तो मुझे "
            "बताइए!"
        ),
        "offer_not_found": (
            "मुझे आपके लिए कोई हाल का ऑफ़र लेटर नहीं मिला। एडमिशन में मैं आपकी "
            "क्या मदद कर सकती हूँ?"
        ),
        "yes_confirmed_program": (
            "बढ़िया {name}! *{program}* के लिए आपकी पुष्टि हो गई है। एडमिशन आगे "
            "बढ़ाने के लिए कहिए: 'I want to take admission'"
        ),
        "offer_missing_fields": (
            "आपका ऑफ़र लेटर बनाने से पहले मुझे यह अभी चाहिए: {fields}।"
        ),
        "offer_no_documents": (
            "मुझे अभी तक आपसे कोई दस्तावेज़ नहीं मिला। कृपया अपनी "
            "ट्रांसक्रिप्ट/मार्कशीट और आईडी प्रमाण की साफ़ फ़ोटो या PDF भेजें, "
            "फिर 'done' टाइप करें।"
        ),
        "offer_success": (
            "🎓 खुशखबरी, {name}! {program} के लिए आपका ऑफ़र लेटर तैयार है — "
            "मैंने इसे यहाँ WhatsApp पर भेज दिया है और आपको ईमेल पर भी एक कॉपी "
            "भेजी है। तैयार होने पर 'accept' या 'decline' लिखकर जवाब दें।"
        ),
        "offer_failure": (
            "क्षमा करें, मैं अभी आपका ऑफ़र लेटर नहीं बना सकी। कृपया थोड़ी देर "
            "में फिर कोशिश करें।"
        ),
        "rag_failure": (
            "क्षमा करें, मैं आपका सवाल प्रोसेस नहीं कर सकी। कृपया फिर से कोशिश "
            "करें।"
        ),
        "safety_fallback": (
            "मेरे पास आपका नाम {name} है। मेरिडियन एडमिशन में मैं आपकी क्या मदद "
            "कर सकती हूँ?"
        ),
    },
    "gu": {
        "media_audio_unsupported": (
            "હું વોઇસ નોંધ સાંભળી શકતી નથી. કૃપા કરીને તમારો પ્રશ્ન અહીં "
            "ચેટમાં ટાઇપ કરો, હું તરત જવાબ આપીશ."
        ),
        "media_document_received": (
            "📄 તમારું દસ્તાવેજ મળી ગયું! હું તેને પ્રોસેસ કરી રહી છું. જો "
            "વધુ દસ્તાવેજો હોય તો તે પણ મોકલો, અથવા પૂર્ણ થાય ત્યારે 'done' "
            "ટાઇપ કરો."
        ),
        "media_unknown_type": (
            "મને તમારી ફાઇલ મળી, પણ હું ફક્ત ફોટા (ટ્રાન્સક્રિપ્ટ, આઇડી) અને "
            "PDF સ્વીકારી શકું છું. કૃપા કરીને તેને ઇમેજ અથવા PDF તરીકે મોકલો."
        ),
        "empty_message": (
            "નમસ્તે! મેરિડિયન યુનિવર્સિટી એડમિશન વિશે તમારો પ્રશ્ન મોકલો."
        ),
        "greeting_known_name": (
            "નમસ્તે {name}! 👋 હું મેરિડિયન યુનિવર્સિટીની એડમિશન સહાયિકા છું. "
            "તમે શું જાણવા માગો છો?"
        ),
        "greeting_unknown_name": (
            "નમસ્તે! 👋 હું મેરિડિયન યુનિવર્સિટીની એડમિશન સહાયિકા છું. તમારું "
            "નામ શું છે?"
        ),
        "name_corrected_ask_email": (
            "આભાર — મેં તમારું નામ {name} કરી દીધું છે. તમારું ઈમેલ સરનામું "
            "શું છે?"
        ),
        "name_corrected_help": (
            "આભાર — મેં તમારું નામ {name} કરી દીધું છે. એડમિશનમાં હું તમારી "
            "શું મદદ કરી શકું?"
        ),
        "name_change_prompt": (
            "અલબત્ત — તમે તમારું નામ શું રાખવા માગો છો? (તમે ફક્ત નામ લખીને "
            "મોકલી શકો છો.)"
        ),
        "name_saved_help": (
            "આભાર — હવે તમારું નામ {name} છે. એડમિશનમાં હું તમારી શું મદદ કરી "
            "શકું?"
        ),
        "name_not_caught": (
            "માફ કરશો, મને નામ સમજાયું નહીં. તેને શું બદલવું છે?"
        ),
        "update_name_prompt": "ચોક્કસ — તમે તમારું નામ શું બદલવા માગો છો?",
        "name_captured_ask_email": (
            "આભાર {name}! તમારું ઈમેલ સરનામું શું છે? હું તેનો ઉપયોગ તમને "
            "કાર્યક્રમની માહિતી મોકલવા અને આગળ સંપર્ક કરવા માટે કરીશ."
        ),
        "name_captured_profile_updated": (
            "આભાર {name}! મેં તમારી પ્રોફાઇલ અપડેટ કરી દીધી છે. મેરિડિયન "
            "એડમિશનમાં હું તમારી શું મદદ કરી શકું?"
        ),
        "ask_name_missing": (
            "નમસ્તે! મદદ કરતા પહેલા, શું તમે તમારું નામ કહી શકશો?"
        ),
        "email_saved_with_name": "આભાર {name}! મેં તમારું ઈમેલ સાચવી લીધું છે.",
        "email_saved_no_name": "આભાર — મેં તમારું ઈમેલ સાચવી લીધું છે.",
        "update_email_saved": "આભાર — મેં તમારું ઈમેલ સરનામું અપડેટ કરી દીધું છે.",
        "update_email_prompt": "ચોક્કસ — સાચું ઈમેલ સરનામું શું છે?",
        "email_captured_ask_name": "તમારું ઈમેલ મળી ગયું! અને તમારું નામ શું છે?",
        "email_captured_help": (
            "આભાર! મેં તમારું ઈમેલ સાચવી લીધું છે. આજે એડમિશનમાં હું તમારી "
            "શું મદદ કરી શકું?"
        ),
        "email_missing_ask": (
            "નમસ્તે {name}! શું તમે તમારું ઈમેલ સરનામું શેર કરી શકશો? હું તેનો "
            "ઉપયોગ તમને કાર્યક્રમની માહિતી મોકલવા અને પછી સંપર્ક કરવા માટે "
            "કરીશ."
        ),
        "program_detected": (
            "**{program}** — સમજી ગઈ! 🎓\n\nએડમિશન આગળ વધારવા માટે કહો: "
            "'I want to take admission' અને હું તમને દસ્તાવેજ અપલોડની પ્રક્રિયા "
            "જણાવીશ."
        ),
        "program_choice": (
            "**{program}** — સરસ પસંદગી! 🎓\n\nએડમિશન આગળ વધારવા માટે કહો: "
            "'I want to take admission' અને હું તમને દસ્તાવેજ અપલોડની પ્રક્રિયા "
            "જણાવીશ."
        ),
        "program_ask_awaiting": (
            "તમે કયો કાર્યક્રમ ઇચ્છો છો? (જેમ કે B.Tech Computer Science, MBA, "
            "M.Tech, BCA)"
        ),
        "program_ask_interested": (
            "તમને કયા કાર્યક્રમમાં રસ છે? (જેમ કે B.Tech Computer Science, MBA, "
            "M.Tech, BCA)"
        ),
        "program_ask_update": (
            "ચોક્કસ — તમે તેના બદલે કયો કાર્યક્રમ ઇચ્છો છો? (જેમ કે B.Tech "
            "Computer Science, MBA, M.Tech, BCA)"
        ),
        "admission_docs_prompt": (
            "સરસ! *{program}* માં તમારા એડમિશન માટે કૃપા કરીને આ દસ્તાવેજો "
            "અપલોડ કરો:\n\n📄 ટ્રાન્સક્રિપ્ટ / માર્કશીટ\n🆔 આઇડી પ્રૂફ "
            "(પાસપોર્ટ, આધાર અથવા ડ્રાઇવિંગ લાઇસન્સ)\n📝 કોઈપણ વધારાના "
            "પ્રમાણપત્રો (વૈકલ્પિક)\n\nસ્પષ્ટ ફોટા અથવા PDF સીધા અહીં WhatsApp "
            "પર મોકલો. બધું મોકલ્યા પછી 'done' ટાઇપ કરો."
        ),
        "update_field_prompt": (
            "કોઈ વાંધો નહીં! તમે શું અપડેટ કરવા માગો છો? તમારું નામ, ઈમેલ અથવા "
            "કાર્યક્રમની રુચિ?"
        ),
        "update_field_unknown_choice": (
            "તમે તમારું નામ, ઈમેલ અથવા કાર્યક્રમની રુચિ અપડેટ કરી શકો છો — કયું?"
        ),
        "offer_accepted": (
            "🎉 અભિનંદન {name}! *{program}* માટે તમારી ઓફર સ્વીકારાઈ ગઈ છે. "
            "તમારી સીટ પક્કી કરવા માટે ટૂંક સમયમાં તમને પેમેન્ટ લિંક મળશે."
        ),
        "offer_declined": (
            "સમજી ગઈ, {name}. *{program}* માટે તમારી ઓફર નકારી દેવાઈ છે. જો "
            "તમે મન બદલો અથવા બીજા કાર્યક્રમો જોવા માગો, તો મને જણાવો!"
        ),
        "offer_not_found": (
            "મને તમારા માટે કોઈ તાજેતરનો ઓફર લેટર મળ્યો નથી. એડમિશનમાં હું "
            "તમારી શું મદદ કરી શકું?"
        ),
        "yes_confirmed_program": (
            "સરસ {name}! *{program}* માટે તમારી પુષ્ટિ થઈ ગઈ છે. એડમિશન આગળ "
            "વધારવા માટે કહો: 'I want to take admission'"
        ),
        "offer_missing_fields": (
            "તમારો ઓફર લેટર બનાવતા પહેલા મને આ હજી જોઈએ: {fields}."
        ),
        "offer_no_documents": (
            "મને હજી સુધી તમારી પાસેથી કોઈ દસ્તાવેજ મળ્યો નથી. કૃપા કરીને તમારી "
            "ટ્રાન્સક્રિપ્ટ/માર્કશીટ અને આઇડી પ્રૂફના સ્પષ્ટ ફોટા અથવા PDF "
            "મોકલો, પછી 'done' ટાઇપ કરો."
        ),
        "offer_success": (
            "🎓 ખુશખબર, {name}! {program} માટે તમારો ઓફર લેટર તૈયાર છે — મેં "
            "તેને અહીં WhatsApp પર મોકલ્યો છે અને તમને ઈમેલ પર પણ કૉપી મોકલી છે. "
            "તૈયાર હો ત્યારે 'accept' અથવા 'decline' લખીને જવાબ આપો."
        ),
        "offer_failure": (
            "માફ કરશો, હું અત્યારે તમારો ઓફર લેટર બનાવી શકી નથી. કૃપા કરીને "
            "થોડી વાર પછી ફરી પ્રયાસ કરો."
        ),
        "rag_failure": (
            "માફ કરશો, હું તમારો પ્રશ્ન પ્રોસેસ કરી શકી નથી. કૃપા કરીને ફરી "
            "પ્રયાસ કરો."
        ),
        "safety_fallback": (
            "મારી પાસે તમારું નામ {name} છે. મેરિડિયન એડમિશનમાં હું તમારી શું "
            "મદદ કરી શકું?"
        ),
    },
    "es": {
        "media_audio_unsupported": (
            "No puedo escuchar notas de voz. Escriba su pregunta aquí en el chat "
            "y le responderé enseguida."
        ),
        "media_document_received": (
            "📄 ¡Recibí su documento! Lo estoy procesando. Si tiene más "
            "documentos, envíelos también, o escriba 'done' cuando termine."
        ),
        "media_unknown_type": (
            "Recibí su archivo, pero solo puedo aceptar fotos (transcripciones, "
            "identificaciones) y PDF. Por favor, envíelo como imagen o PDF."
        ),
        "empty_message": (
            "¡Hola! Envíe su pregunta sobre las admisiones de Meridian University."
        ),
        "greeting_known_name": (
            "¡Hola, {name}! 👋 Soy la asistente de admisiones de Meridian "
            "University. ¿Qué desea saber?"
        ),
        "greeting_unknown_name": (
            "¡Hola! 👋 Soy la asistente de admisiones de Meridian University. "
            "¿Cuál es su nombre?"
        ),
        "name_corrected_ask_email": (
            "Gracias — he corregido su nombre a {name}. ¿Cuál es su correo "
            "electrónico?"
        ),
        "name_corrected_help": (
            "Gracias — he corregido su nombre a {name}. ¿En qué puedo ayudarle "
            "con su admisión?"
        ),
        "name_change_prompt": (
            "Claro — ¿a qué nombre quiere cambiarlo? (Puede responder solo con "
            "el nombre)."
        ),
        "name_saved_help": (
            "Gracias — su nombre ahora es {name}. ¿En qué puedo ayudarle con su "
            "admisión?"
        ),
        "name_not_caught": (
            "Lo siento, no entendí el nombre. ¿A qué debería cambiarlo?"
        ),
        "update_name_prompt": "Claro — ¿a qué nombre quiere cambiarlo?",
        "name_captured_ask_email": (
            "¡Gracias, {name}! ¿Cuál es su correo electrónico? Lo usaré para "
            "enviarle información de los programas y hacer seguimiento."
        ),
        "name_captured_profile_updated": (
            "¡Gracias, {name}! He actualizado su perfil. ¿En qué puedo ayudarle "
            "con las admisiones de Meridian?"
        ),
        "ask_name_missing": "¡Hola! Antes de ayudarle, ¿me dice su nombre?",
        "email_saved_with_name": "¡Gracias, {name}! He guardado su correo electrónico.",
        "email_saved_no_name": "Gracias — he guardado su correo electrónico.",
        "update_email_saved": "Gracias — he actualizado su correo electrónico.",
        "update_email_prompt": "Claro — ¿cuál es el correo electrónico correcto?",
        "email_captured_ask_name": "¡Recibí su correo! ¿Y cuál es su nombre?",
        "email_captured_help": (
            "¡Gracias! He guardado su correo electrónico. ¿En qué puedo ayudarle "
            "hoy con las admisiones?"
        ),
        "email_missing_ask": (
            "¡Hola, {name}! ¿Podría compartir su correo electrónico? Lo usaré "
            "para enviarle información de los programas y hacer seguimiento más "
            "adelante."
        ),
        "program_detected": (
            "**{program}** — ¡entendido! 🎓\n\nPara continuar con su admisión, "
            "diga: 'I want to take admission' y le guiaré en el proceso de carga "
            "de documentos."
        ),
        "program_choice": (
            "**{program}** — ¡excelente elección! 🎓\n\nPara continuar con su "
            "admisión, diga: 'I want to take admission' y le guiaré en el "
            "proceso de carga de documentos."
        ),
        "program_ask_awaiting": (
            "¿Qué programa desea? (por ejemplo, B.Tech Computer Science, MBA, "
            "M.Tech, BCA)"
        ),
        "program_ask_interested": (
            "¿Qué programa le interesa? (por ejemplo, B.Tech Computer Science, "
            "MBA, M.Tech, BCA)"
        ),
        "program_ask_update": (
            "Claro — ¿qué programa desea en su lugar? (por ejemplo, B.Tech "
            "Computer Science, MBA, M.Tech, BCA)"
        ),
        "admission_docs_prompt": (
            "¡Genial! Para procesar su admisión a *{program}*, cargue los "
            "siguientes documentos:\n\n📄 Expediente académico / boletín de "
            "notas\n🆔 Identificación (pasaporte, Aadhaar o licencia de "
            "conducir)\n📝 Certificados adicionales (opcional)\n\nEnvíe fotos "
            "claras o PDF aquí mismo en WhatsApp. Escriba 'done' cuando haya "
            "enviado todo."
        ),
        "update_field_prompt": (
            "¡No hay problema! ¿Qué desea actualizar? ¿Su nombre, correo "
            "electrónico o programa de interés?"
        ),
        "update_field_unknown_choice": (
            "Puede actualizar su nombre, correo electrónico o programa de "
            "interés — ¿cuál?"
        ),
        "offer_accepted": (
            "🎉 ¡Felicidades, {name}! Su oferta para *{program}* ha sido "
            "aceptada. Recibirá un enlace de pago en breve para confirmar su "
            "plaza."
        ),
        "offer_declined": (
            "Entendido, {name}. Su oferta para *{program}* ha sido rechazada. "
            "Si cambia de opinión o quiere explorar otros programas, ¡avíseme!"
        ),
        "offer_not_found": (
            "No encontré una carta de oferta reciente para usted. ¿En qué puedo "
            "ayudarle con su admisión?"
        ),
        "yes_confirmed_program": (
            "¡Genial, {name}! Su plaza en *{program}* está confirmada. Para "
            "continuar con la admisión, diga: 'I want to take admission'"
        ),
        "offer_missing_fields": (
            "Antes de generar su carta de oferta, todavía necesito: {fields}."
        ),
        "offer_no_documents": (
            "Aún no veo documentos suyos. Envíe fotos claras o PDF de su "
            "expediente/boletín de notas e identificación, y luego escriba "
            "'done'."
        ),
        "offer_success": (
            "🎓 ¡Buenas noticias, {name}! Su carta de oferta para {program} "
            "está lista — se la he enviado aquí en WhatsApp y también le he "
            "enviado una copia por correo. Responda 'accept' o 'decline' cuando "
            "esté listo."
        ),
        "offer_failure": (
            "No pude generar su carta de oferta en este momento. Inténtelo de "
            "nuevo en un momento."
        ),
        "rag_failure": (
            "Lo siento, no pude procesar su pregunta. Inténtelo de nuevo."
        ),
        "safety_fallback": (
            "Le tengo registrado como {name}. ¿En qué puedo ayudarle con las "
            "admisiones de Meridian?"
        ),
    },
    "fr": {
        "media_audio_unsupported": (
            "Je ne peux pas écouter les messages vocaux. Tapez votre question "
            "ici dans le chat et je vous répondrai tout de suite."
        ),
        "media_document_received": (
            "📄 J'ai bien reçu votre document ! Je le traite. Si vous avez "
            "d'autres documents, envoyez-les aussi, ou tapez 'done' lorsque "
            "vous avez terminé."
        ),
        "media_unknown_type": (
            "J'ai reçu votre fichier, mais je n'accepte que les photos "
            "(relevés, pièces d'identité) et les PDF. Merci de l'envoyer en "
            "image ou en PDF."
        ),
        "empty_message": (
            "Bonjour ! Envoyez votre question sur les admissions à Meridian "
            "University."
        ),
        "greeting_known_name": (
            "Bonjour {name} ! 👋 Je suis l'assistante des admissions de "
            "Meridian University. Que souhaitez-vous savoir ?"
        ),
        "greeting_unknown_name": (
            "Bonjour ! 👋 Je suis l'assistante des admissions de Meridian "
            "University. Quel est votre nom ?"
        ),
        "name_corrected_ask_email": (
            "Merci — j'ai corrigé votre nom en {name}. Quelle est votre adresse "
            "e-mail ?"
        ),
        "name_corrected_help": (
            "Merci — j'ai corrigé votre nom en {name}. Comment puis-je vous "
            "aider pour votre admission ?"
        ),
        "name_change_prompt": (
            "Bien sûr — quel nom souhaitez-vous ? (Vous pouvez simplement "
            "répondre avec le nom.)"
        ),
        "name_saved_help": (
            "Merci — votre nom est maintenant {name}. Comment puis-je vous "
            "aider pour votre admission ?"
        ),
        "name_not_caught": (
            "Désolée, je n'ai pas compris le nom. Par quel nom faut-il le "
            "remplacer ?"
        ),
        "update_name_prompt": "Bien sûr — quel nom souhaitez-vous ?",
        "name_captured_ask_email": (
            "Merci {name} ! Quelle est votre adresse e-mail ? Elle me servira à "
            "vous envoyer les informations sur les programmes et à assurer le "
            "suivi."
        ),
        "name_captured_profile_updated": (
            "Merci {name} ! J'ai mis à jour votre profil. Comment puis-je vous "
            "aider pour les admissions à Meridian ?"
        ),
        "ask_name_missing": (
            "Bonjour ! Avant de vous aider, pourriez-vous me dire votre nom ?"
        ),
        "email_saved_with_name": "Merci {name} ! J'ai enregistré votre adresse e-mail.",
        "email_saved_no_name": "Merci — j'ai enregistré votre adresse e-mail.",
        "update_email_saved": "Merci — j'ai mis à jour votre adresse e-mail.",
        "update_email_prompt": "Bien sûr — quelle est la bonne adresse e-mail ?",
        "email_captured_ask_name": "J'ai bien reçu votre e-mail ! Et quel est votre nom ?",
        "email_captured_help": (
            "Merci ! J'ai enregistré votre adresse e-mail. Comment puis-je vous "
            "aider aujourd'hui pour les admissions ?"
        ),
        "email_missing_ask": (
            "Bonjour {name} ! Pourriez-vous partager votre adresse e-mail ? "
            "Elle me servira à vous envoyer les informations sur les programmes "
            "et à assurer le suivi."
        ),
        "program_detected": (
            "**{program}** — c'est noté ! 🎓\n\nPour poursuivre votre "
            "admission, dites : 'I want to take admission' et je vous guiderai "
            "pour le téléversement des documents."
        ),
        "program_choice": (
            "**{program}** — excellent choix ! 🎓\n\nPour poursuivre votre "
            "admission, dites : 'I want to take admission' et je vous guiderai "
            "pour le téléversement des documents."
        ),
        "program_ask_awaiting": (
            "Quel programme souhaitez-vous ? (par exemple, B.Tech Computer "
            "Science, MBA, M.Tech, BCA)"
        ),
        "program_ask_interested": (
            "Quel programme vous intéresse ? (par exemple, B.Tech Computer "
            "Science, MBA, M.Tech, BCA)"
        ),
        "program_ask_update": (
            "Bien sûr — quel programme souhaitez-vous à la place ? (par "
            "exemple, B.Tech Computer Science, MBA, M.Tech, BCA)"
        ),
        "admission_docs_prompt": (
            "Parfait ! Pour traiter votre admission en *{program}*, veuillez "
            "téléverser les documents suivants :\n\n📄 Relevé de notes\n🆔 "
            "Pièce d'identité (passeport, Aadhaar ou permis de conduire)\n📝 "
            "Certificats supplémentaires (facultatif)\n\nEnvoyez des photos "
            "nettes ou des PDF directement ici sur WhatsApp. Tapez 'done' "
            "lorsque vous avez tout envoyé."
        ),
        "update_field_prompt": (
            "Pas de problème ! Que souhaitez-vous mettre à jour ? Votre nom, "
            "votre adresse e-mail ou votre programme d'intérêt ?"
        ),
        "update_field_unknown_choice": (
            "Vous pouvez mettre à jour votre nom, votre adresse e-mail ou votre "
            "programme d'intérêt — lequel ?"
        ),
        "offer_accepted": (
            "🎉 Félicitations {name} ! Votre offre pour *{program}* a été "
            "acceptée. Vous recevrez bientôt un lien de paiement pour confirmer "
            "votre place."
        ),
        "offer_declined": (
            "Entendu, {name}. Votre offre pour *{program}* a été refusée. Si "
            "vous changez d'avis ou souhaitez explorer d'autres programmes, "
            "faites-le-moi savoir !"
        ),
        "offer_not_found": (
            "Je n'ai pas trouvé de lettre d'offre récente pour vous. Comment "
            "puis-je vous aider pour votre admission ?"
        ),
        "yes_confirmed_program": (
            "Parfait {name} ! Votre place en *{program}* est confirmée. Pour "
            "poursuivre l'admission, dites : 'I want to take admission'"
        ),
        "offer_missing_fields": (
            "Avant de générer votre lettre d'offre, il me manque encore : "
            "{fields}."
        ),
        "offer_no_documents": (
            "Je ne vois pas encore de documents de votre part. Envoyez des "
            "photos nettes ou des PDF de votre relevé de notes et de votre "
            "pièce d'identité, puis tapez 'done'."
        ),
        "offer_success": (
            "🎓 Bonne nouvelle, {name} ! Votre lettre d'offre pour {program} "
            "est prête — je vous l'ai envoyée ici sur WhatsApp et une copie par "
            "e-mail. Répondez 'accept' ou 'decline' lorsque vous êtes prêt."
        ),
        "offer_failure": (
            "Je n'ai pas pu générer votre lettre d'offre pour le moment. "
            "Veuillez réessayer dans un instant."
        ),
        "rag_failure": (
            "Désolée, je n'ai pas pu traiter votre question. Veuillez réessayer."
        ),
        "safety_fallback": (
            "Vous êtes enregistré sous le nom {name}. Comment puis-je vous "
            "aider pour les admissions à Meridian ?"
        ),
    },
    "it": {
        "media_audio_unsupported": (
            "Non posso ascoltare i messaggi vocali. Scriva la sua domanda qui "
            "nella chat e le risponderò subito."
        ),
        "media_document_received": (
            "📄 Ho ricevuto il suo documento! Lo sto elaborando. Se ha altri "
            "documenti, li invii pure, oppure scriva 'done' quando ha finito."
        ),
        "media_unknown_type": (
            "Ho ricevuto il suo file, ma posso accettare solo foto (transcript, "
            "documenti d'identità) e PDF. La prego di inviarlo come immagine o "
            "PDF."
        ),
        "empty_message": (
            "Salve! Invii la sua domanda sulle ammissioni a Meridian University."
        ),
        "greeting_known_name": (
            "Salve {name}! 👋 Sono l'assistente per le ammissioni di Meridian "
            "University. Cosa desidera sapere?"
        ),
        "greeting_unknown_name": (
            "Salve! 👋 Sono l'assistente per le ammissioni di Meridian "
            "University. Qual è il suo nome?"
        ),
        "name_corrected_ask_email": (
            "Grazie — ho corretto il suo nome in {name}. Qual è il suo indirizzo "
            "e-mail?"
        ),
        "name_corrected_help": (
            "Grazie — ho corretto il suo nome in {name}. Come posso aiutarla "
            "con la sua ammissione?"
        ),
        "name_change_prompt": (
            "Certamente — come vuole che sia il suo nome? (Può rispondere "
            "semplicemente con il nome.)"
        ),
        "name_saved_help": (
            "Grazie — ora il suo nome è {name}. Come posso aiutarla con la sua "
            "ammissione?"
        ),
        "name_not_caught": (
            "Mi dispiace, non ho capito il nome. In cosa dovrei cambiarlo?"
        ),
        "update_name_prompt": "Certamente — come vuole che sia il suo nome?",
        "name_captured_ask_email": (
            "Grazie {name}! Qual è il suo indirizzo e-mail? Lo userò per "
            "inviarle le informazioni sui programmi e per il seguito."
        ),
        "name_captured_profile_updated": (
            "Grazie {name}! Ho aggiornato il suo profilo. Come posso aiutarla "
            "con le ammissioni a Meridian?"
        ),
        "ask_name_missing": (
            "Salve! Prima di aiutarla, mi può dire il suo nome?"
        ),
        "email_saved_with_name": "Grazie {name}! Ho salvato il suo indirizzo e-mail.",
        "email_saved_no_name": "Grazie — ho salvato il suo indirizzo e-mail.",
        "update_email_saved": "Grazie — ho aggiornato il suo indirizzo e-mail.",
        "update_email_prompt": "Certamente — qual è l'indirizzo e-mail corretto?",
        "email_captured_ask_name": "Ho ricevuto la sua e-mail! E qual è il suo nome?",
        "email_captured_help": (
            "Grazie! Ho salvato il suo indirizzo e-mail. Come posso aiutarla "
            "oggi con le ammissioni?"
        ),
        "email_missing_ask": (
            "Salve {name}! Può condividere il suo indirizzo e-mail? Lo userò per "
            "inviarle le informazioni sui programmi e per il seguito."
        ),
        "program_detected": (
            "**{program}** — capito! 🎓\n\nPer procedere con la sua "
            "ammissione, dica: 'I want to take admission' e la guiderò nel "
            "caricamento dei documenti."
        ),
        "program_choice": (
            "**{program}** — ottima scelta! 🎓\n\nPer procedere con la sua "
            "ammissione, dica: 'I want to take admission' e la guiderò nel "
            "caricamento dei documenti."
        ),
        "program_ask_awaiting": (
            "Quale programma desidera? (ad esempio B.Tech Computer Science, "
            "MBA, M.Tech, BCA)"
        ),
        "program_ask_interested": (
            "Quale programma le interessa? (ad esempio B.Tech Computer Science, "
            "MBA, M.Tech, BCA)"
        ),
        "program_ask_update": (
            "Certamente — quale programma desidera invece? (ad esempio B.Tech "
            "Computer Science, MBA, M.Tech, BCA)"
        ),
        "admission_docs_prompt": (
            "Ottimo! Per elaborare la sua ammissione a *{program}*, carichi i "
            "seguenti documenti:\n\n📄 Transcript / pagella\n🆔 Documento "
            "d'identità (passaporto, Aadhaar o patente)\n📝 Certificati "
            "aggiuntivi (facoltativo)\n\nInvii foto nitide o PDF direttamente "
            "qui su WhatsApp. Scriva 'done' quando ha inviato tutto."
        ),
        "update_field_prompt": (
            "Nessun problema! Cosa desidera aggiornare? Il suo nome, il suo "
            "indirizzo e-mail o il programma di interesse?"
        ),
        "update_field_unknown_choice": (
            "Può aggiornare il suo nome, il suo indirizzo e-mail o il programma "
            "di interesse — quale?"
        ),
        "offer_accepted": (
            "🎉 Congratulazioni {name}! La sua offerta per *{program}* è stata "
            "accettata. Riceverà a breve un link di pagamento per confermare il "
            "suo posto."
        ),
        "offer_declined": (
            "Ho capito, {name}. La sua offerta per *{program}* è stata "
            "rifiutata. Se cambia idea o desidera esplorare altri programmi, me "
            "lo faccia sapere!"
        ),
        "offer_not_found": (
            "Non ho trovato una lettera di offerta recente per lei. Come posso "
            "aiutarla con la sua ammissione?"
        ),
        "yes_confirmed_program": (
            "Ottimo {name}! Il suo posto in *{program}* è confermato. Per "
            "procedere con l'ammissione, dica: 'I want to take admission'"
        ),
        "offer_missing_fields": (
            "Prima di generare la sua lettera di offerta, mi serve ancora: "
            "{fields}."
        ),
        "offer_no_documents": (
            "Non vedo ancora documenti da parte sua. Invii foto nitide o PDF "
            "del suo transcript/pagella e del documento d'identità, poi scriva "
            "'done'."
        ),
        "offer_success": (
            "🎓 Buone notizie, {name}! La sua lettera di offerta per {program} "
            "è pronta — gliel'ho inviata qui su WhatsApp e le ho inviato una "
            "copia via e-mail. Risponda 'accept' o 'decline' quando è pronto."
        ),
        "offer_failure": (
            "Non sono riuscita a generare la sua lettera di offerta in questo "
            "momento. Riprovi tra poco."
        ),
        "rag_failure": (
            "Mi dispiace, non sono riuscita a elaborare la sua domanda. Riprovi."
        ),
        "safety_fallback": (
            "La ho registrata come {name}. Come posso aiutarla con le "
            "ammissioni a Meridian?"
        ),
    },
    "ja": {
        "media_audio_unsupported": (
            "音声メッセージは聞き取れません。チャットに質問を入力してください。"
            "すぐにお答えします。"
        ),
        "media_document_received": (
            "📄 書類を受け取りました！処理しています。他にも書類があれば送って"
            "ください。すべて送り終えたら「done」と入力してください。"
        ),
        "media_unknown_type": (
            "ファイルを受け取りましたが、受け付けられるのは写真（成績証明書、"
            "身分証明書）とPDFのみです。画像またはPDFでお送りください。"
        ),
        "empty_message": (
            "こんにちは！Meridian Universityの入学に関するご質問をお送り"
            "ください。"
        ),
        "greeting_known_name": (
            "こんにちは、{name}さん！👋 Meridian Universityの入学アドバイザー"
            "です。何をお知りになりたいですか？"
        ),
        "greeting_unknown_name": (
            "こんにちは！👋 Meridian Universityの入学アドバイザーです。お名前"
            "を教えていただけますか？"
        ),
        "name_corrected_ask_email": (
            "ありがとうございます。お名前を{name}に修正しました。メールアドレス"
            "を教えていただけますか？"
        ),
        "name_corrected_help": (
            "ありがとうございます。お名前を{name}に修正しました。入学について"
            "どのようなご支援が必要ですか？"
        ),
        "name_change_prompt": (
            "もちろんです。お名前はどのように変更しますか？（お名前だけ返信して"
            "いただいても構いません。）"
        ),
        "name_saved_help": (
            "ありがとうございます。お名前は{name}になりました。入学について"
            "どのようなご支援が必要ですか？"
        ),
        "name_not_caught": (
            "申し訳ありません、お名前を確認できませんでした。どのように変更"
            "しますか？"
        ),
        "update_name_prompt": "もちろんです。お名前はどのように変更しますか？",
        "name_captured_ask_email": (
            "{name}さん、ありがとうございます！メールアドレスを教えていただけ"
            "ますか？プログラムの詳細をお送りし、今後ご連絡するために使用します。"
        ),
        "name_captured_profile_updated": (
            "{name}さん、ありがとうございます！プロフィールを更新しました。"
            "Meridianの入学についてどのようなご支援が必要ですか？"
        ),
        "ask_name_missing": (
            "こんにちは！お手伝いの前に、お名前を教えていただけますか？"
        ),
        "email_saved_with_name": (
            "{name}さん、ありがとうございます！メールアドレスを保存しました。"
        ),
        "email_saved_no_name": "ありがとうございます。メールアドレスを保存しました。",
        "update_email_saved": "ありがとうございます。メールアドレスを更新しました。",
        "update_email_prompt": "もちろんです。正しいメールアドレスを教えていただけますか？",
        "email_captured_ask_name": (
            "メールアドレスを受け取りました！お名前を教えていただけますか？"
        ),
        "email_captured_help": (
            "ありがとうございます！メールアドレスを保存しました。本日の入学に"
            "ついてどのようなご支援が必要ですか？"
        ),
        "email_missing_ask": (
            "{name}さん、こんにちは！メールアドレスを共有していただけますか？"
            "プログラムの詳細をお送りし、後ほどご連絡するために使用します。"
        ),
        "program_detected": (
            "**{program}** — 承知しました！🎓\n\n入学手続きを進めるには、"
            "「I want to take admission」と送ってください。書類のアップロード"
            "方法をご案内します。"
        ),
        "program_choice": (
            "**{program}** — 素晴らしい選択です！🎓\n\n入学手続きを進めるには、"
            "「I want to take admission」と送ってください。書類のアップロード"
            "方法をご案内します。"
        ),
        "program_ask_awaiting": (
            "どのプログラムをご希望ですか？（例：B.Tech Computer Science、"
            "MBA、M.Tech、BCA）"
        ),
        "program_ask_interested": (
            "どのプログラムにご興味がありますか？（例：B.Tech Computer "
            "Science、MBA、M.Tech、BCA）"
        ),
        "program_ask_update": (
            "もちろんです。代わりにどのプログラムをご希望ですか？（例：B.Tech "
            "Computer Science、MBA、M.Tech、BCA）"
        ),
        "admission_docs_prompt": (
            "素晴らしいです！*{program}*への入学手続きのため、以下の書類を"
            "アップロードしてください：\n\n📄 成績証明書\n🆔 身分証明書"
            "（パスポート、Aadhaar、運転免許証）\n📝 その他の証明書（任意）\n\n"
            "WhatsAppに鮮明な写真またはPDFを直接お送りください。すべて送り"
            "終えたら「done」と入力してください。"
        ),
        "update_field_prompt": (
            "問題ありません！何を更新しますか？お名前、メールアドレス、希望"
            "プログラムのいずれですか？"
        ),
        "update_field_unknown_choice": (
            "お名前、メールアドレス、希望プログラムを更新できます。どれに"
            "しますか？"
        ),
        "offer_accepted": (
            "🎉 おめでとうございます、{name}さん！*{program}*のオファーが"
            "承諾されました。席を確定するためのお支払いリンクを近日中にお送り"
            "します。"
        ),
        "offer_declined": (
            "承知しました、{name}さん。*{program}*のオファーは辞退されました。"
            "気が変わった場合や他のプログラムを検討されたい場合は、お知らせ"
            "ください！"
        ),
        "offer_not_found": (
            "最近のオファーレターが見つかりませんでした。入学についてどのような"
            "ご支援が必要ですか？"
        ),
        "yes_confirmed_program": (
            "素晴らしいです、{name}さん！*{program}*の席が確定しました。入学"
            "手続きを進めるには、「I want to take admission」と送ってください。"
        ),
        "offer_missing_fields": (
            "オファーレターを作成する前に、以下が必要です：{fields}。"
        ),
        "offer_no_documents": (
            "まだ書類が届いていません。成績証明書と身分証明書の鮮明な写真または"
            "PDFをお送りください。送信後「done」と入力してください。"
        ),
        "offer_success": (
            "🎓 良いお知らせです、{name}さん！{program}のオファーレターが準備"
            "できました。WhatsAppでお送りし、メールでもコピーをお送りしました。"
            "準備ができたら「accept」または「decline」と返信してください。"
        ),
        "offer_failure": (
            "申し訳ありません、現在オファーレターを作成できませんでした。"
            "しばらくしてからもう一度お試しください。"
        ),
        "rag_failure": (
            "申し訳ありません、ご質問を処理できませんでした。もう一度お試し"
            "ください。"
        ),
        "safety_fallback": (
            "{name}さんとして登録されています。Meridianの入学についてどのような"
            "ご支援が必要ですか？"
        ),
    },
    "pt": {
        "media_audio_unsupported": (
            "Não consigo ouvir mensagens de voz. Digite sua pergunta aqui no "
            "chat e responderei em breve."
        ),
        "media_document_received": (
            "📄 Recebi seu documento! Estou processando. Se tiver mais "
            "documentos, envie também, ou digite 'done' quando terminar."
        ),
        "media_unknown_type": (
            "Recebi seu arquivo, mas só posso aceitar fotos (históricos, "
            "documentos de identidade) e PDF. Por favor, envie como imagem ou "
            "PDF."
        ),
        "empty_message": (
            "Olá! Envie sua pergunta sobre as admissões da Meridian University."
        ),
        "greeting_known_name": (
            "Olá, {name}! 👋 Sou a assistente de admissões da Meridian "
            "University. O que você gostaria de saber?"
        ),
        "greeting_unknown_name": (
            "Olá! 👋 Sou a assistente de admissões da Meridian University. Qual "
            "é o seu nome?"
        ),
        "name_corrected_ask_email": (
            "Obrigada — corrigi seu nome para {name}. Qual é o seu e-mail?"
        ),
        "name_corrected_help": (
            "Obrigada — corrigi seu nome para {name}. Como posso ajudar com sua "
            "admissão?"
        ),
        "name_change_prompt": (
            "Claro — para qual nome você quer alterar? (Pode responder apenas "
            "com o nome.)"
        ),
        "name_saved_help": (
            "Obrigada — seu nome agora é {name}. Como posso ajudar com sua "
            "admissão?"
        ),
        "name_not_caught": (
            "Desculpe, não entendi o nome. Para qual nome devo alterar?"
        ),
        "update_name_prompt": "Claro — para qual nome você quer alterar?",
        "name_captured_ask_email": (
            "Obrigada, {name}! Qual é o seu e-mail? Vou usá-lo para enviar "
            "informações sobre os programas e fazer acompanhamento."
        ),
        "name_captured_profile_updated": (
            "Obrigada, {name}! Atualizei seu perfil. Como posso ajudar com as "
            "admissões da Meridian?"
        ),
        "ask_name_missing": (
            "Olá! Antes de ajudar, você pode me dizer seu nome?"
        ),
        "email_saved_with_name": "Obrigada, {name}! Salvei seu e-mail.",
        "email_saved_no_name": "Obrigada — salvei seu e-mail.",
        "update_email_saved": "Obrigada — atualizei seu e-mail.",
        "update_email_prompt": "Claro — qual é o e-mail correto?",
        "email_captured_ask_name": "Recebi seu e-mail! E qual é o seu nome?",
        "email_captured_help": (
            "Obrigada! Salvei seu e-mail. Como posso ajudar hoje com as "
            "admissões?"
        ),
        "email_missing_ask": (
            "Olá, {name}! Você pode compartilhar seu e-mail? Vou usá-lo para "
            "enviar informações sobre os programas e fazer acompanhamento "
            "depois."
        ),
        "program_detected": (
            "**{program}** — entendi! 🎓\n\nPara prosseguir com sua admissão, "
            "diga: 'I want to take admission' e vou orientá-lo no envio dos "
            "documentos."
        ),
        "program_choice": (
            "**{program}** — ótima escolha! 🎓\n\nPara prosseguir com sua "
            "admissão, diga: 'I want to take admission' e vou orientá-lo no "
            "envio dos documentos."
        ),
        "program_ask_awaiting": (
            "Qual programa você deseja? (por exemplo, B.Tech Computer Science, "
            "MBA, M.Tech, BCA)"
        ),
        "program_ask_interested": (
            "Qual programa você tem interesse? (por exemplo, B.Tech Computer "
            "Science, MBA, M.Tech, BCA)"
        ),
        "program_ask_update": (
            "Claro — qual programa você deseja em vez disso? (por exemplo, "
            "B.Tech Computer Science, MBA, M.Tech, BCA)"
        ),
        "admission_docs_prompt": (
            "Ótimo! Para processar sua admissão em *{program}*, envie os "
            "seguintes documentos:\n\n📄 Histórico escolar / boletim\n🆔 "
            "Documento de identidade (passaporte, Aadhaar ou carteira de "
            "motorista)\n📝 Certificados adicionais (opcional)\n\nEnvie fotos "
            "nítidas ou PDF aqui mesmo no WhatsApp. Digite 'done' quando tiver "
            "enviado tudo."
        ),
        "update_field_prompt": (
            "Sem problema! O que você gostaria de atualizar? Seu nome, e-mail ou "
            "programa de interesse?"
        ),
        "update_field_unknown_choice": (
            "Você pode atualizar seu nome, e-mail ou programa de interesse — "
            "qual?"
        ),
        "offer_accepted": (
            "🎉 Parabéns, {name}! Sua oferta para *{program}* foi aceita. Você "
            "receberá um link de pagamento em breve para confirmar sua vaga."
        ),
        "offer_declined": (
            "Entendido, {name}. Sua oferta para *{program}* foi recusada. Se "
            "mudar de ideia ou quiser explorar outros programas, é só me "
            "avisar!"
        ),
        "offer_not_found": (
            "Não encontrei uma carta de oferta recente para você. Como posso "
            "ajudar com sua admissão?"
        ),
        "yes_confirmed_program": (
            "Ótimo, {name}! Sua vaga em *{program}* está confirmada. Para "
            "prosseguir com a admissão, diga: 'I want to take admission'"
        ),
        "offer_missing_fields": (
            "Antes de gerar sua carta de oferta, ainda preciso de: {fields}."
        ),
        "offer_no_documents": (
            "Ainda não vejo documentos seus. Envie fotos nítidas ou PDF do seu "
            "histórico/boletim e documento de identidade, e depois digite "
            "'done'."
        ),
        "offer_success": (
            "🎓 Boa notícia, {name}! Sua carta de oferta para {program} está "
            "pronta — enviei aqui no WhatsApp e também uma cópia por e-mail. "
            "Responda 'accept' ou 'decline' quando estiver pronto."
        ),
        "offer_failure": (
            "Não consegui gerar sua carta de oferta agora. Tente novamente em "
            "instantes."
        ),
        "rag_failure": (
            "Desculpe, não consegui processar sua pergunta. Tente novamente."
        ),
        "safety_fallback": (
            "Tenho você registrado como {name}. Como posso ajudar com as "
            "admissões da Meridian?"
        ),
    },
    "zh": {
        "media_audio_unsupported": (
            "我无法收听语音消息。请在聊天中输入您的问题，我会马上回复。"
        ),
        "media_document_received": (
            "📄 已收到您的文件！正在处理中。如果还有其他文件，也请一并发送；"
            "全部发送完后请回复“done”。"
        ),
        "media_unknown_type": (
            "已收到您的文件，但我只能接收照片（成绩单、身份证件）和 PDF。请以"
            "图片或 PDF 形式发送。"
        ),
        "empty_message": (
            "您好！请发送您关于 Meridian University 招生的问题。"
        ),
        "greeting_known_name": (
            "您好，{name}！👋 我是 Meridian University 的招生助理。您想了解"
            "什么？"
        ),
        "greeting_unknown_name": (
            "您好！👋 我是 Meridian University 的招生助理。请问您的姓名是？"
        ),
        "name_corrected_ask_email": (
            "谢谢 — 我已将您的姓名更正为 {name}。请问您的电子邮箱是？"
        ),
        "name_corrected_help": (
            "谢谢 — 我已将您的姓名更正为 {name}。关于入学申请，我能为您做"
            "什么？"
        ),
        "name_change_prompt": (
            "当然 — 您想改成什么名字？（直接回复姓名即可。）"
        ),
        "name_saved_help": (
            "谢谢 — 您的姓名现在是 {name}。关于入学申请，我能为您做什么？"
        ),
        "name_not_caught": "抱歉，我没听清姓名。应该改成什么？",
        "update_name_prompt": "当然 — 您想改成什么名字？",
        "name_captured_ask_email": (
            "谢谢您，{name}！请问您的电子邮箱是？我会用它向您发送课程详情并"
            "跟进。"
        ),
        "name_captured_profile_updated": (
            "谢谢您，{name}！我已更新您的资料。关于 Meridian 的入学申请，我能"
            "为您做什么？"
        ),
        "ask_name_missing": "您好！在帮助您之前，请告诉我您的姓名好吗？",
        "email_saved_with_name": "谢谢您，{name}！我已保存您的电子邮箱。",
        "email_saved_no_name": "谢谢 — 我已保存您的电子邮箱。",
        "update_email_saved": "谢谢 — 我已更新您的电子邮箱。",
        "update_email_prompt": "当然 — 正确的电子邮箱是什么？",
        "email_captured_ask_name": "已收到您的邮箱！请问您的姓名是？",
        "email_captured_help": (
            "谢谢！我已保存您的电子邮箱。今天关于入学申请，我能为您做什么？"
        ),
        "email_missing_ask": (
            "您好，{name}！可以分享您的电子邮箱吗？我会用它向您发送课程详情"
            "并稍后跟进。"
        ),
        "program_detected": (
            "**{program}** — 明白了！🎓\n\n要继续办理入学，请回复：'I want to "
            "take admission'，我会指导您上传文件。"
        ),
        "program_choice": (
            "**{program}** — 很好的选择！🎓\n\n要继续办理入学，请回复："
            "'I want to take admission'，我会指导您上传文件。"
        ),
        "program_ask_awaiting": (
            "您想申请哪个课程？（例如 B.Tech Computer Science、MBA、M.Tech、"
            "BCA）"
        ),
        "program_ask_interested": (
            "您对哪个课程感兴趣？（例如 B.Tech Computer Science、MBA、M.Tech、"
            "BCA）"
        ),
        "program_ask_update": (
            "当然 — 您想换成哪个课程？（例如 B.Tech Computer Science、MBA、"
            "M.Tech、BCA）"
        ),
        "admission_docs_prompt": (
            "很好！为办理 *{program}* 的入学，请上传以下文件：\n\n📄 成绩单\n"
            "🆔 身份证明（护照、Aadhaar 或驾照）\n📝 其他证书（可选）\n\n请直接"
            "在 WhatsApp 中发送清晰的照片或 PDF。全部发送完后请回复“done”。"
        ),
        "update_field_prompt": (
            "没问题！您想更新什么？姓名、电子邮箱还是感兴趣的课程？"
        ),
        "update_field_unknown_choice": (
            "您可以更新姓名、电子邮箱或感兴趣的课程 — 是哪一项？"
        ),
        "offer_accepted": (
            "🎉 恭喜您，{name}！您对 *{program}* 的录取通知已接受。您很快会"
            "收到付款链接以确认名额。"
        ),
        "offer_declined": (
            "明白了，{name}。您对 *{program}* 的录取通知已拒绝。如果您改变"
            "主意或想了解其他课程，请告诉我！"
        ),
        "offer_not_found": (
            "我没有找到您最近的录取通知书。关于入学申请，我能为您做什么？"
        ),
        "yes_confirmed_program": (
            "很好，{name}！您在 *{program}* 的名额已确认。要继续办理入学，请"
            "回复：'I want to take admission'"
        ),
        "offer_missing_fields": (
            "在生成录取通知书之前，我还需要：{fields}。"
        ),
        "offer_no_documents": (
            "我还没有看到您的文件。请发送成绩单和身份证明的清晰照片或 PDF，"
            "然后回复“done”。"
        ),
        "offer_success": (
            "🎓 好消息，{name}！您 {program} 的录取通知书已准备好 — 我已通过 "
            "WhatsApp 发送，并给您发送了电子邮件副本。准备好后请回复"
            "“accept”或“decline”。"
        ),
        "offer_failure": (
            "抱歉，我现在无法生成您的录取通知书。请稍后再试。"
        ),
        "rag_failure": "抱歉，我无法处理您的问题。请重试。",
        "safety_fallback": (
            "我记录您的姓名是 {name}。关于 Meridian 的入学申请，我能为您做"
            "什么？"
        ),
    },
}


OFFER_FIELD_LABELS: dict[str, dict[str, str]] = {
    "hi": {
        "name": "आपका पूरा नाम",
        "email": "आपका ईमेल पता",
        "phone_number": "आपका फ़ोन नंबर",
        "program_interest": "आपकी रुचि का कार्यक्रम",
        "document": "कम से कम एक दस्तावेज़ (ट्रांसक्रिप्ट, आईडी या मार्कशीट)",
        "lead": "आपकी प्रोफ़ाइल",
    },
    "gu": {
        "name": "તમારું પૂરું નામ",
        "email": "તમારું ઈમેલ સરનામું",
        "phone_number": "તમારો ફોન નંબર",
        "program_interest": "તમારી રુચિનો કાર્યક્રમ",
        "document": "ઓછામાં ઓછું એક દસ્તાવેજ (ટ્રાન્સક્રિપ્ટ, આઇડી અથવા માર્કશીટ)",
        "lead": "તમારી પ્રોફાઇલ",
    },
    "es": {
        "name": "su nombre completo",
        "email": "su correo electrónico",
        "phone_number": "su número de teléfono",
        "program_interest": "el programa que le interesa",
        "document": "al menos un documento (expediente, identificación o boletín de notas)",
        "lead": "su perfil",
    },
    "fr": {
        "name": "votre nom complet",
        "email": "votre adresse e-mail",
        "phone_number": "votre numéro de téléphone",
        "program_interest": "le programme qui vous intéresse",
        "document": "au moins un document (relevé de notes, pièce d'identité ou bulletin)",
        "lead": "votre profil",
    },
    "it": {
        "name": "il suo nome completo",
        "email": "il suo indirizzo e-mail",
        "phone_number": "il suo numero di telefono",
        "program_interest": "il programma che le interessa",
        "document": "almeno un documento (transcript, documento d'identità o pagella)",
        "lead": "il suo profilo",
    },
    "ja": {
        "name": "お名前（フルネーム）",
        "email": "メールアドレス",
        "phone_number": "電話番号",
        "program_interest": "ご希望のプログラム",
        "document": "少なくとも1つの書類（成績証明書、身分証明書、または成績表）",
        "lead": "プロフィール",
    },
    "pt": {
        "name": "seu nome completo",
        "email": "seu e-mail",
        "phone_number": "seu número de telefone",
        "program_interest": "o programa de seu interesse",
        "document": "pelo menos um documento (histórico, documento de identidade ou boletim)",
        "lead": "seu perfil",
    },
    "zh": {
        "name": "您的全名",
        "email": "您的电子邮箱",
        "phone_number": "您的电话号码",
        "program_interest": "您感兴趣的课程",
        "document": "至少一份文件（成绩单、身份证明或成绩表）",
        "lead": "您的资料",
    },
}

OFFER_LIST_JOIN: dict[str, tuple[str, str]] = {
    "hi": (", ", " और "),
    "gu": (", ", " અને "),
    "es": (", ", " y "),
    "fr": (", ", " et "),
    "it": (", ", " e "),
    "ja": ("、", "と"),
    "pt": (", ", " e "),
    "zh": ("、", "和"),
}


def whatsapp_text(
    key: str, language: str | None, default: str, **kwargs: object
) -> str:
    """Return the WhatsApp prompt in *language*, or the English default.

    English, empty and unknown codes return ``default``. A translated template
    is formatted with *kwargs*; the call sites pass the English literal as a
    template too, so English output goes through the same formatting path and
    stays byte-identical. A malformed translation falls back to the English
    default rather than failing the webhook.
    """
    code = normalize_language_code(language)
    template = WHATSAPP_TEXT.get(code, {}).get(key, default)
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        logger.warning(
            "WhatsApp translation %s:%s failed to format; using English",
            code or "default",
            key,
        )
        try:
            return default.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return default


def whatsapp_offer_fields(
    missing: list[str], language: str | None = ""
) -> str:
    """The offer-readiness list in *language* (English delegates to offers).

    The English path intentionally imports ``missing_fields_text`` lazily so
    the existing test monkeypatch of that function is honoured.
    """
    code = normalize_language_code(language)
    if code not in WHATSAPP_TEXT:
        from app.offers.service import missing_fields_text

        return missing_fields_text(missing)
    if not missing:
        return ""
    labels = [OFFER_FIELD_LABELS[code].get(field, field) for field in missing]
    if len(labels) == 1:
        return labels[0]
    separator, final = OFFER_LIST_JOIN[code]
    return separator.join(labels[:-1]) + final + labels[-1]
