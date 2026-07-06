import json
from pathlib import Path
from typing import Dict, List, Tuple, Any
from collections import defaultdict

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Dash, dcc, html, dash_table, Input, Output, callback_context
from plotly.subplots import make_subplots

BASE_DIR = Path('.')
DEFAULT_INSTANCE = BASE_DIR / 'test01.json'
DEFAULT_PHASE1 = BASE_DIR / 'solucion_fase1.json'
DEFAULT_PHASE2 = BASE_DIR / 'solucion_fase2.json'
DEFAULT_FEEDBACK = BASE_DIR / 'feedback_fase2.json'
DEFAULT_VALIDATOR = BASE_DIR / 'IHTP_Validator.exe'
SOLUTIONS_OFICIALES_DIR = BASE_DIR / 'soluciones_oficiales'

COSTES_COMPETICION = {
    "i01": 3842, "i02": 1264, "i03": 10490, "i04": 1884, "i05": 12760,
    "i06": 10671, "i07": 5026, "i08": 6291, "i09": 6682, "i10": 20820,
    "i11": 25938, "i12": 12430, "i13": 17328, "i14": 9746, "i15": 12486,
    "i16": 10139, "i17": 40535, "i18": 37660, "i19": 44587, "i20": 29098,
    "i21": 24703, "i22": 47861, "i23": 37550, "i24": 33221, "i25": 11517,
    "i26": 64613, "i27": 51828, "i28": 75172, "i29": 12475, "i30": 37943,
}


def load_json(path) -> Dict[str, Any]:
    path = Path(path)
    if path.exists():
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def get_phase_patients(sol: Dict[str, Any]) -> List[Dict[str, Any]]:
    return sol.get('patients', []) if sol else []


def build_patient_lookup(instance: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {p['id']: p for p in instance.get('patients', [])}


def get_days(instance: Dict[str, Any]) -> List[int]:
    return list(range(instance.get('days', 0)))


def get_total_beds(instance: Dict[str, Any]) -> int:
    return sum(r['capacity'] for r in instance.get('rooms', []))


# ============================================================
# VALIDADOR
# ============================================================

def run_validator(instance_path, solution_path, validator_path=DEFAULT_VALIDATOR):
    import subprocess, re
    validator_path = Path(validator_path)
    if not validator_path.exists():
        return None
    solution_path = Path(solution_path)
    if not solution_path.exists():
        return None
    try:
        res = subprocess.run(
            [str(validator_path), str(instance_path), str(solution_path)],
            cwd=str(Path(instance_path).parent),
            capture_output=True, text=True, timeout=120, shell=False
        )
        text = (res.stdout or "") + "\n" + (res.stderr or "")
        return parse_validator_output(text)
    except Exception:
        return None


def parse_validator_output(text):
    import re
    violations = {}
    costs = {}
    total_violations = None
    total_cost = None
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("VIOLATIONS"):
            section = "violations"; continue
        if line.startswith("COSTS"):
            section = "costs"; continue
        if line.startswith("Total violations"):
            m = re.search(r"Total violations\s*=\s*([0-9]+)", line)
            if m: total_violations = int(m.group(1))
            continue
        if line.startswith("Total cost"):
            m = re.search(r"Total cost\s*=\s*([0-9]+)", line)
            if m: total_cost = int(m.group(1))
            continue
        if section == "violations":
            m = re.match(r"([A-Za-z]+)\.*\s*([0-9]+)", line)
            if m: violations[m.group(1)] = int(m.group(2))
        if section == "costs":
            m = re.match(r"([A-Za-z]+)\.*\s*([0-9]+)\s*\(\s*([0-9]+)\s*X\s*([0-9]+)\s*\)", line)
            if m:
                costs[m.group(1)] = {
                    "weighted_cost": int(m.group(2)),
                    "weight": int(m.group(3)),
                    "raw_cost": int(m.group(4)),
                }
    return {
        "violations": violations,
        "costs": costs,
        "total_violations": total_violations,
        "total_cost": total_cost,
    }


# ============================================================
# MÉTRICAS FASE 1
# ============================================================

def compute_phase1_metrics(instance, sol_phase1):
    weights = instance.get('weights', {})
    p_lookup = build_patient_lookup(instance)
    patients = instance.get('patients', [])
    scheduled = get_phase_patients(sol_phase1)

    scheduled_ids = {p['id'] for p in scheduled}
    unscheduled_optional = [p for p in patients if not p.get('mandatory', False) and p['id'] not in scheduled_ids]

    open_ot_days = set()
    surgeon_ot_day = set()
    patient_delay = 0
    admissions_by_day = {}
    surgery_minutes_by_day = {}
    ot_usage = {}
    surgeon_minutes_by_day = {}

    for rec in scheduled:
        pid = rec['id']
        if pid not in p_lookup:
            continue
        p = p_lookup[pid]
        d = rec['admission_day']
        if isinstance(d, str):
            continue
        ot = rec.get('operating_theater')
        sid = p['surgeon_id']
        delay = d - p['surgery_release_day']
        patient_delay += max(0, delay)
        admissions_by_day[d] = admissions_by_day.get(d, 0) + 1
        mins = p['surgery_duration']
        surgery_minutes_by_day[d] = surgery_minutes_by_day.get(d, 0) + mins
        if ot:
            open_ot_days.add((ot, d))
            surgeon_ot_day.add((sid, ot, d))
            ot_usage[(ot, d)] = ot_usage.get((ot, d), 0) + mins
        surgeon_minutes_by_day.setdefault(sid, {})
        surgeon_minutes_by_day[sid][d] = surgeon_minutes_by_day[sid].get(d, 0) + mins

    cost_open_ot = weights.get('open_operating_theater', 0) * len(open_ot_days)
    cost_patient_delay = weights.get('patient_delay', 0) * patient_delay
    cost_unscheduled = weights.get('unscheduled_optional', 0) * len(unscheduled_optional)
    cost_transfer = weights.get('surgeon_transfer', 0) * len(surgeon_ot_day)

    total = cost_open_ot + cost_patient_delay + cost_unscheduled + cost_transfer

    df_day = pd.DataFrame({'day': get_days(instance)})
    df_day['admissions'] = df_day['day'].map(admissions_by_day).fillna(0).astype(int)
    df_day['surgery_minutes'] = df_day['day'].map(surgery_minutes_by_day).fillna(0).astype(int)
    df_day['ot_open_count'] = df_day['day'].apply(lambda d: sum(1 for ot, dd in open_ot_days if dd == d))
    df_day['ot_capacity_total'] = df_day['day'].apply(lambda d: sum(ot['availability'][d] for ot in instance.get('operating_theaters', [])))

    ot_rows = []
    for ot in instance.get('operating_theaters', []):
        oid = ot['id']
        for d in get_days(instance):
            used = ot_usage.get((oid, d), 0)
            capacity = ot['availability'][d]
            if used > 0 or capacity > 0:
                ot_rows.append({'operating_theater': oid, 'day': d, 'used_minutes': used, 'capacity': capacity})
    df_ot = pd.DataFrame(ot_rows) if ot_rows else pd.DataFrame(columns=['operating_theater', 'day', 'used_minutes', 'capacity'])

    surgeon_rows = []
    for s in instance.get('surgeons', []):
        sid = s['id']
        for d in get_days(instance):
            used = surgeon_minutes_by_day.get(sid, {}).get(d, 0)
            cap = s['max_surgery_time'][d] if d < len(s['max_surgery_time']) else 0
            surgeon_rows.append({'surgeon': sid, 'day': d, 'used_minutes': used, 'max_minutes': cap})
    df_surgeon = pd.DataFrame(surgeon_rows) if surgeon_rows else pd.DataFrame(columns=['surgeon', 'day', 'used_minutes', 'max_minutes'])

    return {
        'scheduled_count': len(scheduled),
        'unscheduled_optional_count': len(unscheduled_optional),
        'open_ot_count': len(open_ot_days),
        'surgeon_transfer_count': len(surgeon_ot_day),
        'patient_delay_units': patient_delay,
        'cost_open_ot': cost_open_ot,
        'cost_patient_delay': cost_patient_delay,
        'cost_unscheduled': cost_unscheduled,
        'cost_transfer': cost_transfer,
        'phase1_total_cost': total,
        'df_day': df_day,
        'df_ot': df_ot,
        'df_surgeon': df_surgeon,
        'unscheduled_optional_ids': [p['id'] for p in unscheduled_optional],
    }


# ============================================================
# OCUPACIÓN Y CAMAS
# ============================================================

def compute_occupancy(instance, sol):
    p_lookup = build_patient_lookup(instance)
    rooms = {r['id']: r for r in instance.get('rooms', [])}
    days = get_days(instance)

    occ_records = []
    for room_id in rooms:
        for d in days:
            occ_records.append({
                'room': room_id, 'day': d, 'patients': [], 'genders': set(),
                'ages': [], 'count': 0, 'capacity': rooms[room_id]['capacity'], 'source': []
            })
    occ_map = {(r['room'], r['day']): r for r in occ_records}

    for oc in instance.get('occupants', []):
        rid = oc['room_id']
        for d in range(min(oc['length_of_stay'], instance.get('days', 0))):
            if (rid, d) in occ_map:
                row = occ_map[(rid, d)]
                row['patients'].append(oc['id'])
                row['genders'].add(oc['gender'])
                row['ages'].append(oc['age_group'])
                row['count'] += 1
                row['source'].append('occupant')

    for rec in get_phase_patients(sol):
        pid = rec['id']
        if pid not in p_lookup:
            continue
        p = p_lookup[pid]
        rid = rec.get('room')
        if not rid or rid not in rooms:
            continue
        d0 = rec['admission_day']
        if isinstance(d0, str):
            continue
        los = p['length_of_stay']
        for d in range(d0, min(d0 + los, instance.get('days', 0))):
            if (rid, d) in occ_map:
                row = occ_map[(rid, d)]
                row['patients'].append(pid)
                row['genders'].add(p['gender'])
                row['ages'].append(p['age_group'])
                row['count'] += 1
                row['source'].append('planned')

    occ_df = pd.DataFrame(occ_records)
    occ_df['gender_mix_violation'] = occ_df['genders'].apply(lambda s: 1 if len(s) > 1 else 0)
    occ_df['capacity_violation'] = (occ_df['count'] - occ_df['capacity']).clip(lower=0)
    occ_df['patient_list'] = occ_df['patients'].apply(lambda xs: ', '.join(xs))
    occ_df['is_occupied'] = (occ_df['count'] > 0).astype(int)

    day_records = []
    for d in days:
        day_subset = occ_df[occ_df['day'] == d]
        day_records.append({
            'day': d,
            'occupied_beds': int(day_subset['count'].sum()),
            'occupied_rooms': int((day_subset['count'] > 0).sum()),
            'gender_mix_rooms': int(day_subset['gender_mix_violation'].sum()),
            'capacity_excess': int(day_subset['capacity_violation'].sum()),
        })
    day_df = pd.DataFrame(day_records)
    return occ_df, day_df


def compute_room_age_mix(instance, sol_phase2):
    occ_df, day_df = compute_occupancy(instance, sol_phase2)
    age_groups = instance.get('age_groups', [])
    age_map = {age: i for i, age in enumerate(age_groups)} if age_groups else {}

    def age_range(ages):
        vals = [age_map[a] for a in ages if a in age_map]
        return max(vals) - min(vals) if vals else 0

    occ_df['age_mix'] = occ_df['ages'].apply(age_range)
    weights = instance.get('weights', {})
    total_age_mix = int(occ_df['age_mix'].sum())
    cost_age_mix = total_age_mix * weights.get('room_mixed_age', 0)
    day_age = occ_df.groupby('day', as_index=False)['age_mix'].sum().rename(columns={'age_mix': 'room_age_mix'})
    day_df = day_df.merge(day_age, on='day', how='left').fillna({'room_age_mix': 0})
    return {
        'occ_df': occ_df, 'day_df': day_df,
        'room_age_mix_units': total_age_mix, 'room_age_mix_cost': cost_age_mix,
    }


# ============================================================
# MÉTRICAS DE ENFERMERÍA (FASE 3)
# ============================================================

def compute_nurse_metrics(instance, sol):
    nurses_block = sol.get('nurses', [])
    if not nurses_block:
        return None

    days = get_days(instance)
    shift_types = instance.get('shift_types', ['early', 'late', 'night'])
    rooms = {r['id']: r for r in instance.get('rooms', [])}
    nurses_inst = {n['id']: n for n in instance.get('nurses', [])}
    p_lookup = build_patient_lookup(instance)

    nurse_assignments = defaultdict(list)
    for nb in nurses_block:
        nid = nb['id']
        for a in nb.get('assignments', []):
            d = a['day']
            t = a['shift']
            for r in a.get('rooms', []):
                nurse_assignments[(d, t, r)] = nid

    rooms_per_nurse_shift = defaultdict(int)
    shifts_per_nurse = defaultdict(int)
    for nb in nurses_block:
        nid = nb['id']
        for a in nb.get('assignments', []):
            rooms_per_nurse_shift[(nid, a['day'], a['shift'])] = len(a.get('rooms', []))
            shifts_per_nurse[nid] += 1

    nurse_room_rows = []
    for nb in nurses_block:
        nid = nb['id']
        skill = nurses_inst.get(nid, {}).get('skill_level', 0)
        for a in nb.get('assignments', []):
            nurse_room_rows.append({
                'nurse': nid, 'day': a['day'], 'shift': a['shift'],
                'num_rooms': len(a.get('rooms', [])), 'skill_level': skill,
            })
    df_nurse_load = pd.DataFrame(nurse_room_rows) if nurse_room_rows else pd.DataFrame(
        columns=['nurse', 'day', 'shift', 'num_rooms', 'skill_level'])

    nurse_summary_rows = []
    for nb in nurses_block:
        nid = nb['id']
        skill = nurses_inst.get(nid, {}).get('skill_level', 0)
        total_rooms = sum(len(a.get('rooms', [])) for a in nb.get('assignments', []))
        total_shifts = len(nb.get('assignments', []))
        nurse_summary_rows.append({
            'nurse': nid, 'skill_level': skill,
            'total_shifts_assigned': total_shifts,
            'total_rooms_covered': total_rooms,
            'avg_rooms_per_shift': round(total_rooms / total_shifts, 2) if total_shifts else 0,
        })
    df_nurse_summary = pd.DataFrame(nurse_summary_rows)

    return {
        'df_nurse_load': df_nurse_load,
        'df_nurse_summary': df_nurse_summary,
        'total_nurse_assignments': len(nurse_room_rows),
    }


# ============================================================
# ANÁLISIS COMPARATIVO CON MEJOR RESULTADO DE LA COMPETICIÓN
# ============================================================

def compute_competition_comparison(instance, instance_path, sol, validator_parsed=None):
    inst_stem = Path(instance_path).stem
    inst_key = inst_stem.replace('test', 'i') if inst_stem.startswith('test') else inst_stem
    competition_cost = COSTES_COMPETICION.get(inst_key)

    if validator_parsed is None:
        return None

    our_cost = validator_parsed.get('total_cost')
    our_violations = validator_parsed.get('total_violations')

    if our_cost is None:
        return None

    result = {
        'instance': inst_key,
        'our_cost': our_cost,
        'our_violations': our_violations,
        'competition_best_cost': competition_cost,
    }

    if competition_cost is not None and our_violations == 0:
        result['gap_absolute'] = our_cost - competition_cost
        result['gap_relative_pct'] = round((our_cost - competition_cost) / competition_cost * 100, 2)
    else:
        result['gap_absolute'] = None
        result['gap_relative_pct'] = None

    return result


# ============================================================
# FEEDBACK
# ============================================================

def compute_feedback_summary(feedback):
    day_penalties = feedback.get('day_penalties', {})
    gender_day_penalties = feedback.get('gender_day_penalties', {})
    day_caps = feedback.get('day_admission_caps', {})
    gender_caps = feedback.get('gender_day_admission_caps', {})

    rows = []
    all_days = set(int(d) for d in day_penalties.keys()) | set(int(d) for d in day_caps.keys())
    for gender_map in gender_day_penalties.values():
        all_days |= set(int(d) for d in gender_map.keys())
    for gender_map in gender_caps.values():
        all_days |= set(int(d) for d in gender_map.keys())

    for d in sorted(all_days):
        rows.append({
            'day': d,
            'day_penalty': float(day_penalties.get(str(d), 0.0)),
            'day_cap': day_caps.get(str(d), None),
            'gender_penalties': '; '.join(
                f"{g}: {vals[str(d)]}" for g, vals in gender_day_penalties.items() if str(d) in vals
            ),
            'gender_caps': '; '.join(
                f"{g}: {vals[str(d)]}" for g, vals in gender_caps.items() if str(d) in vals
            ),
        })
    return {'df_feedback': pd.DataFrame(rows)}


# ============================================================
# DISTRIBUCIÓN DETALLADA POR SOLUCIÓN
# ============================================================

def compute_detailed_distributions(instance, sol):
    p_lookup = build_patient_lookup(instance)
    scheduled = [rec for rec in get_phase_patients(sol) if not isinstance(rec.get('admission_day'), str)]

    gender_counts = defaultdict(int)
    age_counts = defaultdict(int)
    los_values = []
    duration_values = []

    for rec in scheduled:
        pid = rec['id']
        if pid not in p_lookup:
            continue
        p = p_lookup[pid]
        gender_counts[p['gender']] += 1
        age_counts[p['age_group']] += 1
        los_values.append(p['length_of_stay'])
        duration_values.append(p['surgery_duration'])

    room_counts = defaultdict(int)
    ot_counts = defaultdict(int)
    surgeon_counts = defaultdict(int)

    for rec in scheduled:
        pid = rec['id']
        if pid not in p_lookup:
            continue
        p = p_lookup[pid]
        r = rec.get('room')
        ot = rec.get('operating_theater')
        if r:
            room_counts[r] += 1
        if ot:
            ot_counts[ot] += 1
        surgeon_counts[p['surgeon_id']] += 1

    return {
        'gender_counts': dict(gender_counts),
        'age_counts': dict(age_counts),
        'los_values': los_values,
        'duration_values': duration_values,
        'room_counts': dict(room_counts),
        'ot_counts': dict(ot_counts),
        'surgeon_counts': dict(surgeon_counts),
    }


# ============================================================
# UI HELPERS
# ============================================================

def kpi_card(title, value, subtitle='', color=None):
    border_color = color or '#ddd'
    return html.Div([
        html.Div(title, style={'fontSize': '13px', 'color': '#555', 'marginBottom': '4px'}),
        html.Div(str(value), style={'fontSize': '26px', 'fontWeight': '700'}),
        html.Div(subtitle, style={'fontSize': '11px', 'color': '#777', 'marginTop': '2px'}),
    ], style={
        'border': f'1px solid {border_color}',
        'borderLeft': f'4px solid {border_color}',
        'borderRadius': '10px',
        'padding': '12px 16px',
        'backgroundColor': 'white',
        'boxShadow': '0 1px 3px rgba(0,0,0,0.04)',
        'minWidth': '170px',
    })


def violation_card(name, count):
    color = '#28a745' if count == 0 else '#dc3545'
    icon = '✓' if count == 0 else '✗'
    return html.Div([
        html.Span(f'{icon} ', style={'color': color, 'fontWeight': 'bold', 'fontSize': '16px'}),
        html.Span(name, style={'fontSize': '13px'}),
        html.Span(f'  {count}', style={'fontSize': '15px', 'fontWeight': '700', 'marginLeft': '6px', 'color': color}),
    ], style={
        'border': f'1px solid {color}33',
        'borderRadius': '8px',
        'padding': '8px 14px',
        'backgroundColor': f'{color}08',
        'display': 'inline-flex',
        'alignItems': 'center',
        'margin': '3px',
    })


def cost_card(name, weighted, weight, raw):
    return html.Div([
        html.Div(name, style={'fontSize': '12px', 'color': '#555'}),
        html.Div(str(weighted), style={'fontSize': '22px', 'fontWeight': '700'}),
        html.Div(f'{weight} × {raw}', style={'fontSize': '11px', 'color': '#999'}),
    ], style={
        'border': '1px solid #e0e0e0',
        'borderRadius': '8px',
        'padding': '10px 14px',
        'backgroundColor': 'white',
        'minWidth': '140px',
        'textAlign': 'center',
    })


# ============================================================
# DASHBOARD PRINCIPAL
# ============================================================

def build_dashboard(
    instance_path=DEFAULT_INSTANCE,
    phase1_path=DEFAULT_PHASE1,
    phase2_path=DEFAULT_PHASE2,
    feedback_path=DEFAULT_FEEDBACK,
    validator_path=DEFAULT_VALIDATOR,
):
    instance = load_json(instance_path)
    phase1 = load_json(phase1_path)
    phase2 = load_json(phase2_path)
    feedback = load_json(feedback_path)

    if not instance:
        raise FileNotFoundError(f'No se encontró la instancia en {instance_path}')

    phase1_metrics = compute_phase1_metrics(instance, phase1)
    room_metrics = compute_room_age_mix(instance, phase2 if phase2 else {'patients': []})
    feedback_metrics = compute_feedback_summary(feedback)

    validator_result = run_validator(instance_path, phase2_path, validator_path)

    sol_competicion = None
    inst_stem = Path(instance_path).stem
    inst_num = inst_stem.replace('test', '').replace('i', '')
    sol_comp_path = SOLUTIONS_OFICIALES_DIR / f'sol_{inst_num}.json'
    if sol_comp_path.exists():
        sol_competicion = load_json(sol_comp_path)

    validator_competicion = None
    if sol_competicion:
        validator_competicion = run_validator(instance_path, sol_comp_path, validator_path)

    nurse_metrics = compute_nurse_metrics(instance, phase2)
    nurse_metrics_comp = compute_nurse_metrics(instance, sol_competicion) if sol_competicion else None

    distrib = compute_detailed_distributions(instance, phase2) if phase2 else None
    distrib_comp = compute_detailed_distributions(instance, sol_competicion) if sol_competicion else None

    competition_comparison = compute_competition_comparison(
        instance, instance_path, phase2, validator_result
    )

    total_beds = get_total_beds(instance)
    total_patients = len(instance.get('patients', []))
    mandatory = sum(1 for p in instance.get('patients', []) if p.get('mandatory', False))
    optional = total_patients - mandatory

    summary_cost = phase1_metrics['phase1_total_cost'] + room_metrics['room_age_mix_cost']

    app = Dash(__name__)
    app.title = 'Cuadro de mandos TFG - IHTC'

    # === Tab: Resumen ===
    tab_resumen = dcc.Tab(label='Resumen', children=[
        html.Div([
            dcc.Graph(figure=px.bar(
                phase1_metrics['df_day'], x='day', y=['admissions', 'ot_open_count'], barmode='group',
                title='Admisiones y quirófanos abiertos por día'
            )),
            dcc.Graph(figure=px.line(
                room_metrics['day_df'], x='day', y=['occupied_beds', 'occupied_rooms'],
                markers=True, title='Ocupación hospitalaria tras fase 2'
            )),
        ], style={'display': 'grid', 'gridTemplateColumns': '1fr 1fr', 'gap': '12px'}),
        html.Div([
            kpi_card('OpenOperatingTheater', phase1_metrics['cost_open_ot'], f"Unidades: {phase1_metrics['open_ot_count']}"),
            kpi_card('PatientDelay', phase1_metrics['cost_patient_delay'], f"Unidades: {phase1_metrics['patient_delay_units']}"),
            kpi_card('ElectiveUnscheduled', phase1_metrics['cost_unscheduled'], f"Pacientes: {phase1_metrics['unscheduled_optional_count']}"),
            kpi_card('RoomAgeMix', room_metrics['room_age_mix_cost'], f"Unidades: {room_metrics['room_age_mix_units']}"),
        ], style={'display': 'flex', 'gap': '12px', 'flexWrap': 'wrap', 'marginTop': '14px'})
    ])

    # === Tab: Violaciones y costes (desglose del validador) ===
    tab_violaciones_children = []

    if validator_result:
        viols = validator_result.get('violations', {})
        costs = validator_result.get('costs', {})

        tab_violaciones_children.append(html.H3('Nuestra solución'))
        tab_violaciones_children.append(html.Div([
            kpi_card('Total violaciones', validator_result.get('total_violations', '?'), color='#dc3545' if validator_result.get('total_violations', 0) > 0 else '#28a745'),
            kpi_card('Coste total', validator_result.get('total_cost', '?')),
        ], style={'display': 'flex', 'gap': '12px', 'marginBottom': '16px'}))

        tab_violaciones_children.append(html.H4('Restricciones duras (violaciones)'))
        tab_violaciones_children.append(html.Div(
            [violation_card(name, count) for name, count in viols.items()],
            style={'display': 'flex', 'flexWrap': 'wrap', 'gap': '4px', 'marginBottom': '16px'}
        ))

        tab_violaciones_children.append(html.H4('Restricciones blandas (costes)'))
        tab_violaciones_children.append(html.Div(
            [cost_card(name, c['weighted_cost'], c['weight'], c['raw_cost']) for name, c in costs.items()],
            style={'display': 'flex', 'flexWrap': 'wrap', 'gap': '8px', 'marginBottom': '16px'}
        ))

        cost_df = pd.DataFrame([
            {'Componente': name, 'Coste ponderado': c['weighted_cost'], 'Peso': c['weight'], 'Coste bruto': c['raw_cost']}
            for name, c in costs.items()
        ])
        if not cost_df.empty:
            tab_violaciones_children.append(dcc.Graph(figure=px.bar(
                cost_df, x='Componente', y='Coste ponderado', color='Componente',
                title='Desglose de costes blandos — Nuestra solución',
                text='Coste ponderado',
            ).update_traces(textposition='outside')))

    if validator_competicion:
        viols_c = validator_competicion.get('violations', {})
        costs_c = validator_competicion.get('costs', {})

        tab_violaciones_children.append(html.Hr())
        tab_violaciones_children.append(html.H3('Mejor resultado de la competición'))
        tab_violaciones_children.append(html.Div([
            kpi_card('Total violaciones', validator_competicion.get('total_violations', '?'), color='#dc3545' if validator_competicion.get('total_violations', 0) > 0 else '#28a745'),
            kpi_card('Coste total', validator_competicion.get('total_cost', '?')),
        ], style={'display': 'flex', 'gap': '12px', 'marginBottom': '16px'}))

        tab_violaciones_children.append(html.H4('Restricciones duras (violaciones)'))
        tab_violaciones_children.append(html.Div(
            [violation_card(name, count) for name, count in viols_c.items()],
            style={'display': 'flex', 'flexWrap': 'wrap', 'gap': '4px', 'marginBottom': '16px'}
        ))

        tab_violaciones_children.append(html.H4('Restricciones blandas (costes)'))
        tab_violaciones_children.append(html.Div(
            [cost_card(name, c['weighted_cost'], c['weight'], c['raw_cost']) for name, c in costs_c.items()],
            style={'display': 'flex', 'flexWrap': 'wrap', 'gap': '8px', 'marginBottom': '16px'}
        ))

    if validator_result and validator_competicion:
        tab_violaciones_children.append(html.Hr())
        tab_violaciones_children.append(html.H3('Comparativa de costes'))

        all_cost_names = sorted(set(list(validator_result.get('costs', {}).keys()) + list(validator_competicion.get('costs', {}).keys())))
        comp_rows = []
        for name in all_cost_names:
            ours = validator_result.get('costs', {}).get(name, {}).get('weighted_cost', 0)
            theirs = validator_competicion.get('costs', {}).get(name, {}).get('weighted_cost', 0)
            comp_rows.append({'Componente': name, 'Nuestra solución': ours, 'Mejor de la competición': theirs})

        comp_df = pd.DataFrame(comp_rows)
        if not comp_df.empty:
            fig_comp = go.Figure()
            fig_comp.add_trace(go.Bar(name='Nuestra solución', x=comp_df['Componente'], y=comp_df['Nuestra solución'], marker_color='#4c78a8'))
            fig_comp.add_trace(go.Bar(name='Mejor de la competición', x=comp_df['Componente'], y=comp_df['Mejor de la competición'], marker_color='#f58518'))
            fig_comp.update_layout(barmode='group', title='Comparativa de costes blandos', yaxis_title='Coste ponderado')
            tab_violaciones_children.append(dcc.Graph(figure=fig_comp))

        if competition_comparison and competition_comparison.get('gap_relative_pct') is not None:
            gap = competition_comparison['gap_relative_pct']
            gap_color = '#28a745' if gap <= 0 else ('#ff9800' if gap <= 50 else '#dc3545')
            tab_violaciones_children.append(html.Div([
                kpi_card('Gap relativo', f"{gap:+.2f}%", 'vs mejor resultado de la competición', color=gap_color),
                kpi_card('Gap absoluto', competition_comparison.get('gap_absolute', '?'), 'unidades de coste'),
            ], style={'display': 'flex', 'gap': '12px', 'marginTop': '12px'}))

    if not validator_result:
        tab_violaciones_children.append(html.P('No se pudo ejecutar el validador. Comprueba que IHTP_Validator.exe está disponible.'))

    tab_violaciones = dcc.Tab(label='Violaciones y costes', children=[html.Div(tab_violaciones_children, style={'padding': '12px 0'})])

    # === Tab: Fase 1 — Bloque quirúrgico ===
    phase1_children = [
        dcc.Graph(figure=px.bar(
            phase1_metrics['df_day'], x='day', y='admissions',
            title='Admisiones por día', labels={'admissions': 'Pacientes admitidos'}
        )),
        dcc.Graph(figure=px.bar(
            phase1_metrics['df_day'], x='day', y='surgery_minutes',
            title='Minutos quirúrgicos por día', labels={'surgery_minutes': 'Minutos'}
        )),
    ]

    if not phase1_metrics['df_ot'].empty:
        phase1_children.append(dcc.Graph(figure=px.bar(
            phase1_metrics['df_ot'], x='day', y=['used_minutes', 'capacity'],
            facet_row='operating_theater', barmode='overlay',
            title='Uso y capacidad por quirófano'
        )))

    if not phase1_metrics['df_surgeon'].empty:
        phase1_children.append(dcc.Graph(figure=px.bar(
            phase1_metrics['df_surgeon'], x='day', y=['used_minutes', 'max_minutes'],
            facet_row='surgeon', barmode='overlay',
            title='Uso y capacidad por cirujano', color_discrete_sequence=['#636EFA', '#EF553B']
        )))

    tab_fase1 = dcc.Tab(label='Fase 1 · Bloque quirúrgico', children=[html.Div(phase1_children)])

    # === Tab: Fase 2 — Camas y habitaciones ===
    tab_fase2 = dcc.Tab(label='Fase 2 · Camas y habitaciones', children=[
        html.Div([
            dcc.Graph(figure=px.density_heatmap(
                room_metrics['occ_df'], x='day', y='room', z='count', histfunc='avg',
                color_continuous_scale='Blues', title='Mapa de ocupación habitación × día'
            )),
            dcc.Graph(figure=px.bar(
                room_metrics['day_df'], x='day',
                y=['gender_mix_rooms', 'capacity_excess', 'room_age_mix'], barmode='group',
                title='Indicadores de habitaciones por día'
            )),
            dcc.Graph(figure=px.line(
                room_metrics['day_df'], x='day', y='occupied_beds', markers=True,
                title='Camas ocupadas por día vs capacidad total',
            ).add_hline(y=total_beds, line_dash='dash', line_color='red',
                        annotation_text=f'Capacidad total ({total_beds})')),
            html.H4('Detalle de ocupación por habitación y día'),
            dash_table.DataTable(
                data=room_metrics['occ_df'][['room', 'day', 'count', 'capacity', 'gender_mix_violation', 'capacity_violation', 'age_mix', 'patient_list']].to_dict('records'),
                columns=[{'name': c, 'id': c} for c in ['room', 'day', 'count', 'capacity', 'gender_mix_violation', 'capacity_violation', 'age_mix', 'patient_list']],
                page_size=12,
                style_table={'overflowX': 'auto'},
                style_cell={'textAlign': 'left', 'fontSize': 12, 'padding': '6px'},
                style_data_conditional=[
                    {'if': {'filter_query': '{gender_mix_violation} > 0'}, 'backgroundColor': '#ffe0e0'},
                    {'if': {'filter_query': '{capacity_violation} > 0'}, 'backgroundColor': '#fff3cd'},
                ],
            ),
        ])
    ])

    # === Tab: Fase 3 — Enfermería ===
    tab_fase3_children = []
    if nurse_metrics:
        tab_fase3_children.append(html.H3('Asignación de enfermeras — Nuestra solución'))

        tab_fase3_children.append(html.Div([
            kpi_card('Asignaciones totales', nurse_metrics['total_nurse_assignments']),
            kpi_card('Enfermeras activas', len(nurse_metrics['df_nurse_summary'])),
        ], style={'display': 'flex', 'gap': '12px', 'marginBottom': '14px'}))

        if not nurse_metrics['df_nurse_load'].empty:
            tab_fase3_children.append(dcc.Graph(figure=px.box(
                nurse_metrics['df_nurse_load'], x='shift', y='num_rooms', color='shift',
                title='Distribución de habitaciones por enfermera y turno',
                labels={'num_rooms': 'Habitaciones asignadas'}
            )))
            tab_fase3_children.append(dcc.Graph(figure=px.bar(
                nurse_metrics['df_nurse_summary'].sort_values('total_rooms_covered', ascending=False),
                x='nurse', y='total_rooms_covered', color='skill_level',
                title='Habitaciones cubiertas por enfermera (total)',
                labels={'total_rooms_covered': 'Total habitaciones'}
            )))
            load_by_day = nurse_metrics['df_nurse_load'].groupby(['day', 'shift'], as_index=False)['num_rooms'].sum()
            tab_fase3_children.append(dcc.Graph(figure=px.bar(
                load_by_day, x='day', y='num_rooms', color='shift', barmode='stack',
                title='Carga de enfermería por día y turno',
                labels={'num_rooms': 'Total habitaciones cubiertas'}
            )))

        tab_fase3_children.append(html.H4('Resumen por enfermera'))
        tab_fase3_children.append(dash_table.DataTable(
            data=nurse_metrics['df_nurse_summary'].to_dict('records'),
            columns=[{'name': c, 'id': c} for c in nurse_metrics['df_nurse_summary'].columns],
            page_size=15,
            style_table={'overflowX': 'auto'},
            style_cell={'textAlign': 'left', 'fontSize': 12, 'padding': '6px'},
        ))
    else:
        tab_fase3_children.append(html.P('No se detectaron asignaciones de enfermeras en la solución cargada.'))

    if nurse_metrics_comp:
        tab_fase3_children.append(html.Hr())
        tab_fase3_children.append(html.H3('Asignación de enfermeras — Mejor resultado de la competición'))

        tab_fase3_children.append(html.Div([
            kpi_card('Asignaciones totales', nurse_metrics_comp['total_nurse_assignments']),
            kpi_card('Enfermeras activas', len(nurse_metrics_comp['df_nurse_summary'])),
        ], style={'display': 'flex', 'gap': '12px', 'marginBottom': '14px'}))

        if not nurse_metrics_comp['df_nurse_load'].empty:
            tab_fase3_children.append(dcc.Graph(figure=px.box(
                nurse_metrics_comp['df_nurse_load'], x='shift', y='num_rooms', color='shift',
                title='Distribución de habitaciones por enfermera y turno (competición)',
            )))

    tab_fase3 = dcc.Tab(label='Fase 3 · Enfermería', children=[html.Div(tab_fase3_children)])

    # === Tab: Distribuciones ===
    tab_distrib_children = []

    def add_distrib_charts(d, label):
        if d is None:
            return
        tab_distrib_children.append(html.H3(label))
        row1 = []
        if d['gender_counts']:
            df_g = pd.DataFrame([{'Género': k, 'Pacientes': v} for k, v in d['gender_counts'].items()])
            row1.append(dcc.Graph(figure=px.pie(df_g, names='Género', values='Pacientes', title='Pacientes por género')))
        if d['age_counts']:
            df_a = pd.DataFrame([{'Grupo de edad': k, 'Pacientes': v} for k, v in d['age_counts'].items()])
            row1.append(dcc.Graph(figure=px.pie(df_a, names='Grupo de edad', values='Pacientes', title='Pacientes por grupo de edad')))
        if row1:
            tab_distrib_children.append(html.Div(row1, style={'display': 'grid', 'gridTemplateColumns': '1fr 1fr', 'gap': '12px'}))

        row2 = []
        if d['room_counts']:
            df_r = pd.DataFrame([{'Habitación': k, 'Pacientes': v} for k, v in sorted(d['room_counts'].items())])
            row2.append(dcc.Graph(figure=px.bar(df_r, x='Habitación', y='Pacientes', title='Pacientes por habitación', color='Habitación')))
        if d['ot_counts']:
            df_ot = pd.DataFrame([{'Quirófano': k, 'Pacientes': v} for k, v in sorted(d['ot_counts'].items())])
            row2.append(dcc.Graph(figure=px.bar(df_ot, x='Quirófano', y='Pacientes', title='Pacientes por quirófano', color='Quirófano')))
        if row2:
            tab_distrib_children.append(html.Div(row2, style={'display': 'grid', 'gridTemplateColumns': '1fr 1fr', 'gap': '12px'}))

        row3 = []
        if d['surgeon_counts']:
            df_s = pd.DataFrame([{'Cirujano': k, 'Pacientes': v} for k, v in sorted(d['surgeon_counts'].items())])
            row3.append(dcc.Graph(figure=px.bar(df_s, x='Cirujano', y='Pacientes', title='Pacientes por cirujano', color='Cirujano')))
        if d['los_values']:
            df_los = pd.DataFrame({'Estancia (días)': d['los_values']})
            row3.append(dcc.Graph(figure=px.histogram(df_los, x='Estancia (días)', nbins=max(d['los_values']), title='Distribución de estancias')))
        if row3:
            tab_distrib_children.append(html.Div(row3, style={'display': 'grid', 'gridTemplateColumns': '1fr 1fr', 'gap': '12px'}))

        if d['duration_values']:
            df_dur = pd.DataFrame({'Duración cirugía (min)': d['duration_values']})
            tab_distrib_children.append(dcc.Graph(figure=px.histogram(
                df_dur, x='Duración cirugía (min)', nbins=20, title='Distribución de duración quirúrgica'
            )))

    add_distrib_charts(distrib, 'Nuestra solución')
    if distrib_comp:
        tab_distrib_children.append(html.Hr())
        add_distrib_charts(distrib_comp, 'Mejor resultado de la competición')

    tab_distrib = dcc.Tab(label='Distribuciones', children=[html.Div(tab_distrib_children, style={'padding': '12px 0'})])

    # === Tab: Interacción y feedback ===
    tab_feedback = dcc.Tab(label='Interacción y feedback', children=[
        html.Div([
            dcc.Graph(figure=px.bar(
                feedback_metrics['df_feedback'], x='day', y='day_penalty',
                title='Penalización devuelta por fase 2 a fase 1'
            ) if not feedback_metrics['df_feedback'].empty else go.Figure()),
            dcc.Graph(figure=px.bar(
                feedback_metrics['df_feedback'].dropna(subset=['day_cap']), x='day', y='day_cap',
                title='Caps de admisión por día sugeridos por fase 2'
            ) if not feedback_metrics['df_feedback'].empty else go.Figure()),
            html.H4('Resumen del feedback de fase 2'),
            dash_table.DataTable(
                data=feedback_metrics['df_feedback'].to_dict('records'),
                columns=[{'name': c, 'id': c} for c in feedback_metrics['df_feedback'].columns],
                page_size=10,
                style_table={'overflowX': 'auto'},
                style_cell={'textAlign': 'left', 'fontSize': 12, 'padding': '6px'},
            )
        ])
    ])

    # === Tab: Validación completa ===
    tab_validacion_children = []
    tab_validacion_children.append(html.Ul([
        html.Li('Fase 1: quirófanos abiertos, retraso de pacientes, pacientes opcionales no programados.'),
        html.Li('Fase 2: capacidad de habitaciones, mezcla de género, mezcla de edades.'),
        html.Li('Fase 3: cobertura de enfermería, nivel de cualificación, carga excesiva, continuidad asistencial.'),
    ]))

    val_cards = [
        kpi_card('RoomGenderMix', int(room_metrics['occ_df']['gender_mix_violation'].sum()), 'Esperado 0 tras fase 2',
                 color='#28a745' if room_metrics['occ_df']['gender_mix_violation'].sum() == 0 else '#dc3545'),
        kpi_card('RoomCapacity', int(room_metrics['occ_df']['capacity_violation'].sum()), 'Esperado 0 tras fase 2',
                 color='#28a745' if room_metrics['occ_df']['capacity_violation'].sum() == 0 else '#dc3545'),
    ]

    if validator_result:
        viols = validator_result.get('violations', {})
        uncov = viols.get('UncoveredRoom', '?')
        val_cards.append(kpi_card('UncoveredRoom', uncov, 'Esperado 0 tras fase 3',
                                  color='#28a745' if uncov == 0 else '#dc3545'))
        total_v = validator_result.get('total_violations', 0)
        val_cards.append(kpi_card('Total violaciones', total_v, 'Validador oficial',
                                  color='#28a745' if total_v == 0 else '#dc3545'))
        val_cards.append(kpi_card('Coste total', validator_result.get('total_cost', '?'), 'Validador oficial'))
    else:
        val_cards.append(kpi_card('Validador', 'No disponible', 'IHTP_Validator.exe no encontrado'))

    tab_validacion_children.append(html.Div(val_cards, style={'display': 'flex', 'gap': '12px', 'flexWrap': 'wrap', 'marginTop': '12px'}))

    if competition_comparison:
        comp = competition_comparison
        comp_cards = []
        if comp.get('competition_best_cost') is not None:
            comp_cards.append(kpi_card('Mejor de la competición', comp['competition_best_cost'], f"Instancia {comp['instance']}"))
        if comp.get('gap_relative_pct') is not None:
            gap_color = '#28a745' if comp['gap_relative_pct'] <= 0 else '#dc3545'
            comp_cards.append(kpi_card('Gap relativo', f"{comp['gap_relative_pct']:+.2f}%", 'vs mejor resultado de la competición', color=gap_color))
            comp_cards.append(kpi_card('Gap absoluto', comp['gap_absolute'], 'unidades de coste'))
        if comp_cards:
            tab_validacion_children.append(html.Hr())
            tab_validacion_children.append(html.H4('Comparativa con el mejor resultado de la competición'))
            tab_validacion_children.append(html.Div(comp_cards, style={'display': 'flex', 'gap': '12px', 'flexWrap': 'wrap'}))

    tab_validacion = dcc.Tab(label='Validación completa', children=[html.Div(tab_validacion_children)])

    # === Layout ===
    app.layout = html.Div([
        html.H1('Cuadro de mandos de evaluación por etapas'),
        html.P(f'Instancia: {Path(instance_path).name} · Evaluación de la propuesta por fases: bloque quirúrgico, camas, enfermería e interacción.'),

        html.Div([
            kpi_card('Pacientes totales', total_patients, f'Obligatorios: {mandatory} · Opcionales: {optional}'),
            kpi_card('Camas totales', total_beds, f'Horizonte: {instance.get("days", 0)} días'),
            kpi_card('Pacientes programados', phase1_metrics['scheduled_count'], f'No programados: {phase1_metrics["unscheduled_optional_count"]}'),
            kpi_card('Coste parcial actual', summary_cost, 'Fase 1 + RoomAgeMix estimado'),
        ], style={'display': 'flex', 'gap': '12px', 'flexWrap': 'wrap', 'marginBottom': '18px'}),

        dcc.Tabs([
            tab_resumen,
            tab_violaciones,
            tab_fase1,
            tab_fase2,
            tab_fase3,
            tab_distrib,
            tab_feedback,
            tab_validacion,
        ])
    ], style={'fontFamily': 'Arial, sans-serif', 'padding': '20px', 'backgroundColor': '#f7f7f9'})

    return app


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--instance', default=str(DEFAULT_INSTANCE))
    parser.add_argument('--phase1', default=str(DEFAULT_PHASE1))
    parser.add_argument('--phase2', default=str(DEFAULT_PHASE2))
    parser.add_argument('--feedback', default=str(DEFAULT_FEEDBACK))
    parser.add_argument('--validator', default=str(DEFAULT_VALIDATOR))
    parser.add_argument('--port', type=int, default=8050)
    args = parser.parse_args()

    app = build_dashboard(
        instance_path=args.instance,
        phase1_path=args.phase1,
        phase2_path=args.phase2,
        feedback_path=args.feedback,
        validator_path=args.validator,
    )
    app.run(debug=True, port=args.port)
