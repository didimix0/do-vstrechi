"""Внутренние спорные оценки разрешаются автоматически до выдачи результата."""
import re


def has_closing_words(text):
    return bool(re.search(r'понял|понимаю|хорошо|ладно|отмен|отказ|не\s+настаив|до\s+свидан|всего\s+(?:добр|хорош)|спасибо|благодар',text,re.I))


def automatic_review(session):
    return session['materials']['scenario'].get('dialogue_features', {}).get('automatic_review') is True


def review_feedback(evaluation):
    return {
        'criteria_to_resolve': [{'id':c['id'], 'issue':c['reason']} for c in evaluation['criteria'] if c['status']=='requires_review'],
        'coverage_complete':evaluation['coverage_complete'],
        'issues':list(evaluation['warnings']),
        'instruction':'Перепроверь эти пункты по полной истории и материалам. Верни полный результат с конкретными цитатами. Неизвестное собственное обещание — неподтверждённое условие, а не верный факт. Не ставь положительную оценку без свидетельств и не обвиняй сотрудника в технической ошибке оценщика.',
    }
