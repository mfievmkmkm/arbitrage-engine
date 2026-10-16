"""Keep scanner candidates that fail the post-funding NET filter in the diary."""


def skipped_net_candidates(quotes, paired_paper):
    return [
        dict(row, paired_paper=paired_paper, strategy='futures_futures',
             action='SKIP', reason='NET_ESTIMATE_NOT_POSITIVE')
        for row in quotes if not row['hypothetical_edge'] > 0
    ]
