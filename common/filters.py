from datetime import datetime


def get_date_range_from_request(request, prefix='date'):
    from_str = request.GET.get(f'{prefix}_from')
    to_str = request.GET.get(f'{prefix}_to')
    exact = request.GET.get(prefix)

    if exact:
        from_date = to_date = datetime.strptime(exact, "%Y-%m-%d").date()
    else:
        from_date = datetime.strptime(from_str, "%Y-%m-%d").date() if from_str else None
        to_date = datetime.strptime(to_str, "%Y-%m-%d").date() if to_str else None

    return from_date, to_date
