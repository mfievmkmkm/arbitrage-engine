"""Small operator summary; no claim of profitability or execution authority."""
import time


def render(scanner):
    row = getattr(scanner, 'last_diagnostics', None)
    if not row:
        return 'Цикл поиска ещё не завершён; статистика пока недоступна.'
    counters = row.get('counters', {})
    age = max(0, int(time.time() - row['finished_at']))
    out = (f'Последний поиск: {age} с назад · цикл {row["duration_seconds"]:.1f} с\n'
           f'Рынков {row["symbols_selected"]} · стаканов '
           f'{counters.get("books_ok", 0)}/{counters.get("books_requested", 0)} · '
           f'пар {counters.get("pairs_checked", 0)} · кандидатов {row["candidates_returned"]}')
    if counters.get('pairs_stale_or_depth'):
        out += f'\nБез свежести/глубины: {counters["pairs_stale_or_depth"]} пар'
    if counters.get('pairs_no_edge_before_funding'):
        out += f'\nБез спреда после комиссий: {counters["pairs_no_edge_before_funding"]} пар'
    if counters.get('book_errors'):
        out += f'\nОшибки стаканов: {counters["book_errors"]}'
    if counters.get('book_error:RateLimitExceeded'):
        out += f' · лимит API: {counters["book_error:RateLimitExceeded"]}'
    if age > max(60, row['duration_seconds'] * 3):
        out += '\n⚠️ Новый цикл долго не завершается; проверь журнал поиска.'
    return out
