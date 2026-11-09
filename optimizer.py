"""Deterministic synchronized multi-traveler route optimization (minutes after midnight)."""
from ortools.sat.python import cp_model


def solve_trip(trip, max_seconds=12):
    people = trip['people']
    stops = trip['stops']
    matrix = trip['travel_minutes']
    horizon = int(trip.get('end_min', 1200))
    departure = int(trip['start_min'])
    if not people or not stops or len(matrix) != len(stops) + 1 or any(len(row) != len(stops) + 1 for row in matrix):
        raise ValueError('Invalid travelers, stops, or travel matrix dimensions')
    if len({s['id'] for s in stops}) != len(stops):
        raise ValueError('Stop IDs must be unique')
    if any(int(v) < 0 for row in matrix for v in row):
        raise ValueError('Travel times cannot be negative')
    if departure >= horizon:
        raise ValueError('Trip end must be after start')
    model = cp_model.CpModel()
    visit_vars, starts, arrivals, waits, arcs_by_person = {}, {}, {}, {}, {}
    drive_terms, wait_terms, value_terms = [], [], []
    M = max(horizon + max(max(row) for row in matrix) + max(int(s['duration']) for s in stops), 1440)
    for p in people:
        pid = p['id']
        eligible = [idx + 1 for idx, s in enumerate(stops) if pid in s['people']]
        if not eligible:
            continue
        nodes = [0] + eligible
        arcs = []
        arc_map = {}
        for i in nodes:
            for j in nodes:
                if i == j and i == 0:
                    continue
                x = model.NewBoolVar(f'arc_{pid}_{i}_{j}')
                arcs.append((i, j, x))
                arc_map[(i, j)] = x
                if i != j:
                    drive_terms.append(int(matrix[i][j]) * x)
        model.AddCircuit(arcs)
        arcs_by_person[pid] = arc_map
        for i in eligible:
            s = stops[i-1]
            selected = model.NewBoolVar(f'visit_{pid}_{i}')
            model.Add(selected + arc_map[(i,i)] == 1)
            visit_vars[(pid, i)] = selected
            arrival = model.NewIntVar(0, M, f'arrive_{pid}_{i}')
            start = model.NewIntVar(0, M, f'start_{pid}_{i}')
            waiting = model.NewIntVar(0, M, f'wait_{pid}_{i}')
            arrivals[(pid,i)], starts[(pid,i)], waits[(pid,i)] = arrival, start, waiting
            model.Add(start == arrival + waiting).OnlyEnforceIf(selected)
            model.Add(waiting == 0).OnlyEnforceIf(selected.Not())
            model.Add(arrival == 0).OnlyEnforceIf(selected.Not())
            model.Add(start == 0).OnlyEnforceIf(selected.Not())
            model.Add(start >= int(s['open'])).OnlyEnforceIf(selected)
            model.Add(start + int(s['duration']) <= int(s['close'])).OnlyEnforceIf(selected)
            model.Add(start + int(s['duration']) <= horizon).OnlyEnforceIf(selected)
            if s.get('required', True):
                model.Add(selected == 1)
            else:
                value_terms.append(int(s.get('priority', 1)) * selected)
            wait_terms.append(waiting)
        for (i,j), x in arc_map.items():
            if i == j:
                continue
            if i == 0:
                model.Add(arrivals[(pid,j)] >= departure + int(matrix[0][j])).OnlyEnforceIf(x)
            elif j == 0:
                model.Add(starts[(pid,i)] + int(stops[i-1]['duration']) + int(matrix[i][0]) <= horizon).OnlyEnforceIf(x)
            else:
                model.Add(arrivals[(pid,j)] >= starts[(pid,i)] + int(stops[i-1]['duration']) + int(matrix[i][j])).OnlyEnforceIf(x)
    for i, s in enumerate(stops, start=1):
        ps = s['people']
        if len(ps) > 1:
            for other in ps[1:]:
                model.Add(visit_vars[(ps[0],i)] == visit_vars[(other,i)])
                model.Add(starts[(ps[0],i)] == starts[(other,i)]).OnlyEnforceIf(visit_vars[(ps[0],i)])
    # Optional visits are prioritized over travel/wait, with a conservative bound.
    max_cost = len(people) * (len(stops) + 1) * max(max(row) for row in matrix) + len(people) * len(stops) * M
    model.Minimize(sum(drive_terms) + sum(wait_terms) - (max_cost + 1) * sum(value_terms))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max_seconds
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {'status': 'infeasible_or_unknown', 'solver_status': solver.StatusName(status), 'routes': [], 'message': 'No feasible schedule found within the solver limit. Check time windows, required stops, and travel times.'}
    routes = []
    for p in people:
        pid = p['id']
        if pid not in arcs_by_person:
            routes.append({'person': pid, 'name': p['name'], 'visits': [], 'drive_minutes': 0, 'wait_minutes': 0})
            continue
        arc_map = arcs_by_person[pid]
        route = []
        current, seen, driving = 0, set(), 0
        while True:
            nexts = [j for (i,j),x in arc_map.items() if i == current and j != i and solver.Value(x)]
            if not nexts:
                raise RuntimeError('Invalid solver circuit: no outgoing arc')
            nxt = nexts[0]
            driving += int(matrix[current][nxt])
            if nxt == 0:
                break
            if nxt in seen:
                raise RuntimeError('Invalid solver circuit: repeated stop')
            seen.add(nxt)
            s = stops[nxt-1]
            route.append({'id': s['id'], 'name': s['name'], 'arrival': solver.Value(arrivals[(pid,nxt)]), 'start': solver.Value(starts[(pid,nxt)]), 'end': solver.Value(starts[(pid,nxt)]) + int(s['duration']), 'wait': solver.Value(waits[(pid,nxt)]), 'shared': len(s['people']) > 1})
            current = nxt
        routes.append({'person': pid, 'name': p['name'], 'visits': route, 'drive_minutes': driving, 'wait_minutes': sum(v['wait'] for v in route)})
    result = {'status': 'optimal' if status == cp_model.OPTIMAL else 'feasible', 'solver_status': solver.StatusName(status), 'routes': routes}
    result['validation_errors'] = validate_solution(trip, result)
    return result


def validate_solution(trip, result):
    """Independent, post-solve constraint checks, including travel and shared start."""
    errors, stop_by_id = [], {s['id']: (i+1,s) for i,s in enumerate(trip['stops'])}
    seen_by_person = {}
    for route in result['routes']:
        pid, previous, earliest, seen = route['person'], 0, int(trip['start_min']), set()
        for visit in route['visits']:
            i,s = stop_by_id[visit['id']]
            if visit['id'] in seen: errors.append(f'{pid}: duplicate stop {visit["id"]}')
            seen.add(visit['id'])
            if pid not in s['people']: errors.append(f'{pid}: unassigned stop {visit["id"]}')
            if visit['arrival'] < earliest + int(trip['travel_minutes'][previous][i]): errors.append(f'{pid}: impossible travel to {visit["id"]}')
            if visit['start'] < visit['arrival'] or visit['wait'] != visit['start'] - visit['arrival']: errors.append(f'{pid}: incorrect waiting at {visit["id"]}')
            if visit['start'] < int(s['open']) or visit['end'] > int(s['close']): errors.append(f'{pid}: time window violated at {visit["id"]}')
            if visit['end'] != visit['start'] + int(s['duration']): errors.append(f'{pid}: duration violated at {visit["id"]}')
            previous, earliest = i, visit['end']
        if earliest + int(trip['travel_minutes'][previous][0]) > int(trip['end_min']): errors.append(f'{pid}: return exceeds trip end')
        seen_by_person[pid] = {v['id']: v for v in route['visits']}
    for s in trip['stops']:
        present = [s['id'] in seen_by_person.get(pid,{}) for pid in s['people']]
        if s.get('required',True) and not all(present): errors.append(f'{s["id"]}: required stop missing')
        if len(set(present)) > 1: errors.append(f'{s["id"]}: inconsistent shared selection')
        if len(s['people']) > 1 and all(present):
            times = [seen_by_person[pid][s['id']]['start'] for pid in s['people']]
            if len(set(times)) != 1: errors.append(f'{s["id"]}: shared start mismatch')
    return errors
