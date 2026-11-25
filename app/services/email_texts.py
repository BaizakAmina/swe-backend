"""Localized email strings (ru / en / kk). Templates receive them as ``t``.

Placeholders use ``str.format`` names from the template context.
"""

from app.models.enums import Locale

Texts = dict[str, str]

_TEXTS: dict[str, dict[Locale, Texts]] = {
    "verify_email": {
        Locale.RU: {
            "subject": "Подтвердите email — BiletFlow",
            "title": "Подтвердите ваш email",
            "body": "Здравствуйте, {name}! Чтобы завершить регистрацию, подтвердите адрес почты.",
            "cta": "Подтвердить email",
            "note": "Ссылка действует 48 часов.",
        },
        Locale.EN: {
            "subject": "Confirm your email — BiletFlow",
            "title": "Confirm your email",
            "body": "Hi {name}! Please confirm your email address to finish signing up.",
            "cta": "Confirm email",
            "note": "The link is valid for 48 hours.",
        },
        Locale.KK: {
            "subject": "Email растаңыз — BiletFlow",
            "title": "Email мекенжайыңызды растаңыз",
            "body": "Сәлеметсіз бе, {name}! Тіркелуді аяқтау үшін email мекенжайын растаңыз.",
            "cta": "Email растау",
            "note": "Сілтеме 48 сағат жарамды.",
        },
    },
    "password_reset": {
        Locale.RU: {
            "subject": "Сброс пароля — BiletFlow",
            "title": "Сброс пароля",
            "body": "{name}, мы получили запрос на сброс пароля.",
            "cta": "Задать новый пароль",
            "note": "Ссылка действует 1 час. Если это были не вы — просто проигнорируйте письмо.",
        },
        Locale.EN: {
            "subject": "Reset your password — BiletFlow",
            "title": "Reset your password",
            "body": "{name}, we received a request to reset your password.",
            "cta": "Set a new password",
            "note": "The link is valid for 1 hour. If it wasn't you, ignore this email.",
        },
        Locale.KK: {
            "subject": "Құпиясөзді қалпына келтіру — BiletFlow",
            "title": "Құпиясөзді қалпына келтіру",
            "body": "{name}, құпиясөзді қалпына келтіру сұрауы түсті.",
            "cta": "Жаңа құпиясөз орнату",
            "note": "Сілтеме 1 сағат жарамды. Егер бұл сіз болмасаңыз, хатты елемеңіз.",
        },
    },
    "order_confirmation": {
        Locale.RU: {
            "subject": "Заказ подтверждён: {event_title}",
            "title": "Спасибо за заказ!",
            "body": "Заказ на «{event_title}» оформлен. Сумма: {total}. Билеты придут отдельным письмом.",
            "cta": "Мои заказы",
            "note": "Оплата проведена в демонстрационном режиме (симуляция).",
        },
        Locale.EN: {
            "subject": "Order confirmed: {event_title}",
            "title": "Thanks for your order!",
            "body": "Your order for “{event_title}” is confirmed. Total: {total}. Tickets follow in a separate email.",
            "cta": "My orders",
            "note": "Payment was processed in demo mode (simulation).",
        },
        Locale.KK: {
            "subject": "Тапсырыс расталды: {event_title}",
            "title": "Тапсырысыңызға рахмет!",
            "body": "«{event_title}» тапсырысы рәсімделді. Сомасы: {total}. Билеттер бөлек хатпен келеді.",
            "cta": "Менің тапсырыстарым",
            "note": "Төлем демо режимінде өтті (симуляция).",
        },
    },
    "ticket": {
        Locale.RU: {
            "subject": "Ваш билет: {event_title}",
            "title": "Ваш билет готов",
            "body": "{attendee_name}, билет «{ticket_type}» на «{event_title}», {starts_at}. Код билета: {ticket_code}.",
            "cta": "Скачать PDF",
            "note": "Покажите QR-код на входе — с телефона или распечатанным.",
        },
        Locale.EN: {
            "subject": "Your ticket: {event_title}",
            "title": "Your ticket is ready",
            "body": "{attendee_name}, “{ticket_type}” ticket for “{event_title}”, {starts_at}. Ticket code: {ticket_code}.",
            "cta": "Download PDF",
            "note": "Show the QR code at the entrance — on your phone or printed.",
        },
        Locale.KK: {
            "subject": "Сіздің билетіңіз: {event_title}",
            "title": "Билетіңіз дайын",
            "body": "{attendee_name}, «{event_title}» іс-шарасына «{ticket_type}» билеті, {starts_at}. Билет коды: {ticket_code}.",
            "cta": "PDF жүктеу",
            "note": "Кіреберісте QR-кодты көрсетіңіз — телефоннан немесе басылған түрде.",
        },
    },
    "event_cancelled": {
        Locale.RU: {
            "subject": "Событие отменено: {event_title}",
            "title": "Событие отменено",
            "body": "К сожалению, «{event_title}» ({starts_at}) отменено организатором. Ваши билеты больше недействительны.",
            "cta": "Мои заказы",
            "note": "Информация о возврате придёт отдельным письмом.",
        },
        Locale.EN: {
            "subject": "Event cancelled: {event_title}",
            "title": "Event cancelled",
            "body": "Unfortunately “{event_title}” ({starts_at}) was cancelled by the organizer. Your tickets are no longer valid.",
            "cta": "My orders",
            "note": "Refund details will follow in a separate email.",
        },
        Locale.KK: {
            "subject": "Іс-шара тоқтатылды: {event_title}",
            "title": "Іс-шара тоқтатылды",
            "body": "Өкінішке қарай, «{event_title}» ({starts_at}) ұйымдастырушы тарапынан тоқтатылды. Билеттеріңіз жарамсыз.",
            "cta": "Менің тапсырыстарым",
            "note": "Қаражатты қайтару туралы ақпарат бөлек хатпен келеді.",
        },
    },
    "staff_assigned": {
        Locale.RU: {
            "subject": "Вы назначены на событие: {event_title}",
            "title": "Новое назначение",
            "body": "Вас назначили администратором входа на «{event_title}» ({starts_at}). Событие появится в мобильном сканере.",
            "cta": "Открыть BiletFlow",
            "note": "",
        },
        Locale.EN: {
            "subject": "You're on staff: {event_title}",
            "title": "New assignment",
            "body": "You were assigned as door staff for “{event_title}” ({starts_at}). It will appear in the mobile scanner.",
            "cta": "Open BiletFlow",
            "note": "",
        },
        Locale.KK: {
            "subject": "Сіз іс-шараға тағайындалдыңыз: {event_title}",
            "title": "Жаңа тағайындау",
            "body": "Сіз «{event_title}» ({starts_at}) кіреберіс әкімшісі болып тағайындалдыңыз. Іс-шара мобильді сканерде пайда болады.",
            "cta": "BiletFlow ашу",
            "note": "",
        },
    },
    "support_reply": {
        Locale.RU: {
            "subject": "Новый ответ по обращению: {subject_line}",
            "title": "Новый ответ в поддержке",
            "body": "{sender} ответил(а) по обращению «{subject_line}»: {preview}",
            "cta": "Открыть обращение",
            "note": "",
        },
        Locale.EN: {
            "subject": "New reply: {subject_line}",
            "title": "New support reply",
            "body": "{sender} replied to “{subject_line}”: {preview}",
            "cta": "Open case",
            "note": "",
        },
        Locale.KK: {
            "subject": "Өтінішке жаңа жауап: {subject_line}",
            "title": "Қолдау қызметінен жаңа жауап",
            "body": "{sender} «{subject_line}» өтініші бойынша жауап берді: {preview}",
            "cta": "Өтінішті ашу",
            "note": "",
        },
    },
    "refund_processed": {
        Locale.RU: {
            "subject": "Возврат оформлен: {event_title}",
            "title": "Возврат оформлен",
            "body": "Возврат {amount} по заказу на «{event_title}» оформлен. Билеты аннулированы.",
            "cta": "Мои заказы",
            "note": "Демонстрационный режим: реального движения денег нет.",
        },
        Locale.EN: {
            "subject": "Refund processed: {event_title}",
            "title": "Refund processed",
            "body": "A refund of {amount} for your order for “{event_title}” was processed. The tickets were cancelled.",
            "cta": "My orders",
            "note": "Demo mode: no real money was moved.",
        },
        Locale.KK: {
            "subject": "Қаражат қайтарылды: {event_title}",
            "title": "Қаражат қайтарылды",
            "body": "«{event_title}» тапсырысы бойынша {amount} қайтарылды. Билеттер жойылды.",
            "cta": "Менің тапсырыстарым",
            "note": "Демо режимі: нақты ақша қозғалысы жоқ.",
        },
    },
}


# label of the deep link into the mobile app, shown under the main button
APP_LINK_LABEL: dict[Locale, str] = {
    Locale.RU: "Открыть в приложении BiletFlow",
    Locale.EN: "Open in the BiletFlow app",
    Locale.KK: "BiletFlow қосымшасында ашу",
}


def texts(kind: str, locale: Locale | str, context: dict[str, object]) -> Texts:
    by_locale = _TEXTS[kind]
    raw = by_locale.get(Locale(locale), by_locale[Locale.RU])
    safe = {k: str(v) for k, v in context.items()}
    result = {k: v.format(**safe) for k, v in raw.items()}
    result.setdefault("app_cta", APP_LINK_LABEL.get(Locale(locale), APP_LINK_LABEL[Locale.RU]))
    return result
