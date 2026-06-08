import json
from pathlib import Path
from typing import Dict, List, Tuple, Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Dash, dcc, html, dash_table, Input, Output

BASE_DIR = Path('.')
DEFAULT_INSTANCE = BASE_DIR / 'test01.json'
DEFAULT_PHASE1 = BASE_DIR / 'solucion_fase1.json'
DEFAULT_PHASE2 = BASE_DIR / 'solucion_fase2.json'
DEFAULT_FEEDBACK = BASE_DIR / 'feedback_fase2.json'


def load_json(path: Path) -> Dict[str, Any]:
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


def compute_phase1_metrics(instance: Dict[str, Any], sol_phase1: Dict[str, Any]) -> Dict[str, Any]:
    weights = instance.get('weights', {})
    p_lookup = build_patient_lookup(instance)
    surgeons = {s['id']: s for s in instance.get('surgeons', [])}
    ots = {t['id']: t for t in instance.get('operating_theaters', [])}
    patients = instance.get('patients', [])
    scheduled = get_phase_patients(sol_phase1)

    scheduled_ids = {p['id'] for p in scheduled}
    unscheduled_optional = [p for p in patients if not p.get('mandatory', False) and p['id'] not in scheduled_ids]

    # Open OT and surgeon transfer estimates from exported solution
    open_ot_days = set()
    surgeon_ot_day = set()
    patient_delay = 0
    admissions_by_day = {}
    surgery_minutes_by_day = {}
    ot_usage = {}

    for rec in scheduled:
        pid = rec['id']
        if pid not in p_lookup:
            continue
        p = p_lookup[pid]
        d = rec['admission_day']
        ot = rec['operating_theater']
        sid = p['surgeon_id']
        delay = d - p['surgery_release_day']
        patient_delay += max(0, delay)
        admissions_by_day[d] = admissions_by_day.get(d, 0) + 1
        mins = p['surgery_duration']
        surgery_minutes_by_day[d] = surgery_minutes_by_day.get(d, 0) + mins
        open_ot_days.add((ot, d))
        surgeon_ot_day.add((sid, ot, d))
        ot_usage[(ot, d)] = ot_usage.get((ot, d), 0) + mins

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
    df_ot = pd.DataFrame(ot_rows)

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
        'unscheduled_optional_ids': [p['id'] for p in unscheduled_optional],
    }


def compute_occupancy(instance: Dict[str, Any], sol: Dict[str, Any]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    p_lookup = build_patient_lookup(instance)
    rooms = {r['id']: r for r in instance.get('rooms', [])}
    days = get_days(instance)

    occ_records = []
    day_records = []

    # occupants
    for room_id in rooms:
        for d in days:
            occ_records.append({
                'room': room_id,
                'day': d,
                'patients': [],
                'genders': set(),
                'ages': [],
                'count': 0,
                'capacity': rooms[room_id]['capacity'],
                'source': []
            })
    occ_map = {(r['room'], r['day']): r for r in occ_records}

    for oc in instance.get('occupants', []):
        rid = oc['room_id']
        for d in range(min(oc['length_of_stay'], instance.get('days', 0))):
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
        if rid not in rooms:
            continue
        d0 = rec['admission_day']
        los = p['length_of_stay']
        for d in range(d0, min(d0 + los, instance.get('days', 0))):
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


def compute_room_age_mix(instance: Dict[str, Any], sol_phase2: Dict[str, Any]) -> Dict[str, Any]:
    occ_df, day_df = compute_occupancy(instance, sol_phase2)
    age_groups = instance.get('age_groups', [])
    if age_groups:
        age_map = {age: i for i, age in enumerate(age_groups)}
    else:
        ages = sorted(set(instance.get('age_group', []) for _ in []))
        age_map = {age: i for i, age in enumerate(ages)}

    def age_range(ages: List[str]) -> int:
        vals = [age_map[a] for a in ages if a in age_map]
        return max(vals) - min(vals) if vals else 0

    occ_df['age_mix'] = occ_df['ages'].apply(age_range)
    weights = instance.get('weights', {})
    total_age_mix = int(occ_df['age_mix'].sum())
    cost_age_mix = total_age_mix * weights.get('room_mixed_age', 0)
    day_age = occ_df.groupby('day', as_index=False)['age_mix'].sum().rename(columns={'age_mix': 'room_age_mix'})
    day_df = day_df.merge(day_age, on='day', how='left').fillna({'room_age_mix': 0})
    return {
        'occ_df': occ_df,
        'day_df': day_df,
        'room_age_mix_units': total_age_mix,
        'room_age_mix_cost': cost_age_mix,
    }


def compute_feedback_summary(feedback: Dict[str, Any]) -> Dict[str, Any]:
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


def kpi_card(title: str, value: Any, subtitle: str = '') -> html.Div:
    return html.Div(
        [
            html.Div(title, style={'fontSize': '14px', 'color': '#555'}),
            html.Div(str(value), style={'fontSize': '28px', 'fontWeight': '700'}),
            html.Div(subtitle, style={'fontSize': '12px', 'color': '#777'}),
        ],
        style={
            'border': '1px solid #ddd',
            'borderRadius': '12px',
            'padding': '14px 18px',
            'backgroundColor': 'white',
            'boxShadow': '0 1px 3px rgba(0,0,0,0.04)',
            'minWidth': '180px'
        }
    )


def build_dashboard(instance_path=DEFAULT_INSTANCE, phase1_path=DEFAULT_PHASE1, phase2_path=DEFAULT_PHASE2, feedback_path=DEFAULT_FEEDBACK):
    instance = load_json(instance_path)
    phase1 = load_json(phase1_path)
    phase2 = load_json(phase2_path)
    feedback = load_json(feedback_path)

    if not instance:
        raise FileNotFoundError(f'No se encontró la instancia en {instance_path}')

    phase1_metrics = compute_phase1_metrics(instance, phase1)
    room_metrics = compute_room_age_mix(instance, phase2 if phase2 else {'patients': []})
    feedback_metrics = compute_feedback_summary(feedback)

    total_beds = get_total_beds(instance)
    total_patients = len(instance.get('patients', []))
    mandatory = sum(1 for p in instance.get('patients', []) if p.get('mandatory', False))
    optional = total_patients - mandatory

    summary_cost = phase1_metrics['phase1_total_cost'] + room_metrics['room_age_mix_cost']

    app = Dash(__name__)
    app.title = 'Cuadro de mandos TFG - IHTC'

    app.layout = html.Div([
        html.H1('Cuadro de mandos de evaluación por etapas'),
        html.P('Evaluación de la propuesta por fases: bloque quirúrgico, camas e interacción fase 1 ↔ fase 2.'),

        html.Div([
            kpi_card('Pacientes totales', total_patients, f'Obligatorios: {mandatory} · Opcionales: {optional}'),
            kpi_card('Camas totales', total_beds, f'Horizonte: {instance.get("days", 0)} días'),
            kpi_card('Pacientes programados', phase1_metrics['scheduled_count'], f'No programados: {phase1_metrics["unscheduled_optional_count"]}'),
            kpi_card('Coste parcial actual', summary_cost, 'Fase 1 + RoomAgeMix estimado'),
        ], style={'display': 'flex', 'gap': '12px', 'flexWrap': 'wrap', 'marginBottom': '18px'}),

        dcc.Tabs([
            dcc.Tab(label='Resumen', children=[
                html.Div([
                    dcc.Graph(
                        figure=px.bar(
                            phase1_metrics['df_day'], x='day', y=['admissions', 'ot_open_count'], barmode='group',
                            title='Admisiones y quirófanos abiertos por día'
                        )
                    ),
                    dcc.Graph(
                        figure=px.line(
                            room_metrics['day_df'], x='day', y=['occupied_beds', 'occupied_rooms'],
                            markers=True, title='Ocupación hospitalaria tras fase 2'
                        )
                    ),
                ], style={'display': 'grid', 'gridTemplateColumns': '1fr 1fr', 'gap': '12px'}),
                html.Div([
                    kpi_card('OpenOperatingTheater', phase1_metrics['cost_open_ot'], f"Unidades: {phase1_metrics['open_ot_count']}"),
                    kpi_card('PatientDelay', phase1_metrics['cost_patient_delay'], f"Unidades: {phase1_metrics['patient_delay_units']}"),
                    kpi_card('ElectiveUnscheduled', phase1_metrics['cost_unscheduled'], f"Pacientes: {phase1_metrics['unscheduled_optional_count']}"),
                    kpi_card('RoomAgeMix', room_metrics['room_age_mix_cost'], f"Unidades: {room_metrics['room_age_mix_units']}"),
                ], style={'display': 'flex', 'gap': '12px', 'flexWrap': 'wrap', 'marginTop': '14px'})
            ]),

            dcc.Tab(label='Fase 1 · Bloque quirúrgico', children=[
                html.Div([
                    dcc.Graph(
                        figure=px.bar(
                            phase1_metrics['df_day'], x='day', y='admissions',
                            title='Admisiones por día', labels={'admissions': 'Pacientes admitidos'}
                        )
                    ),
                    dcc.Graph(
                        figure=px.bar(
                            phase1_metrics['df_day'], x='day', y='surgery_minutes',
                            title='Minutos quirúrgicos por día', labels={'surgery_minutes': 'Minutos'}
                        )
                    ),
                    dcc.Graph(
                        figure=px.bar(
                            phase1_metrics['df_ot'], x='day', y=['used_minutes', 'capacity'], facet_row='operating_theater',
                            barmode='overlay', title='Uso y capacidad por quirófano'
                        ) if not phase1_metrics['df_ot'].empty else go.Figure()
                    ),
                ])
            ]),

            dcc.Tab(label='Fase 2 · Camas y habitaciones', children=[
                html.Div([
                    dcc.Graph(
                        figure=px.density_heatmap(
                            room_metrics['occ_df'], x='day', y='room', z='count', histfunc='avg',
                            color_continuous_scale='Blues', title='Mapa de ocupación habitación × día'
                        )
                    ),
                    dcc.Graph(
                        figure=px.bar(
                            room_metrics['day_df'], x='day', y=['gender_mix_rooms', 'capacity_excess', 'room_age_mix'], barmode='group',
                            title='Indicadores de habitaciones por día'
                        )
                    ),
                    html.H4('Detalle de ocupación por habitación y día'),
                    dash_table.DataTable(
                        data=room_metrics['occ_df'][['room', 'day', 'count', 'capacity', 'gender_mix_violation', 'capacity_violation', 'age_mix', 'patient_list']].to_dict('records'),
                        columns=[{'name': c, 'id': c} for c in ['room', 'day', 'count', 'capacity', 'gender_mix_violation', 'capacity_violation', 'age_mix', 'patient_list']],
                        page_size=12,
                        style_table={'overflowX': 'auto'},
                        style_cell={'textAlign': 'left', 'fontSize': 12, 'padding': '6px'},
                    ),
                ])
            ]),

            dcc.Tab(label='Interacción y feedback', children=[
                html.Div([
                    dcc.Graph(
                        figure=px.bar(
                            feedback_metrics['df_feedback'], x='day', y='day_penalty',
                            title='Penalización devuelta por fase 2 a fase 1'
                        ) if not feedback_metrics['df_feedback'].empty else go.Figure()
                    ),
                    dcc.Graph(
                        figure=px.bar(
                            feedback_metrics['df_feedback'].dropna(subset=['day_cap']), x='day', y='day_cap',
                            title='Caps de admisión por día sugeridos por fase 2'
                        ) if not feedback_metrics['df_feedback'].empty else go.Figure()
                    ),
                    html.H4('Resumen del feedback de fase 2'),
                    dash_table.DataTable(
                        data=feedback_metrics['df_feedback'].to_dict('records'),
                        columns=[{'name': c, 'id': c} for c in feedback_metrics['df_feedback'].columns],
                        page_size=10,
                        style_table={'overflowX': 'auto'},
                        style_cell={'textAlign': 'left', 'fontSize': 12, 'padding': '6px'},
                    )
                ])
            ]),

            dcc.Tab(label='Validación parcial', children=[
                html.Div([
                    html.Ul([
                        html.Li('Fase 1 evaluada en términos de quirófanos abiertos, retraso de pacientes y pacientes opcionales no programados.'),
                        html.Li('Fase 2 evaluada en términos de capacidad de habitaciones, mezcla de género y mezcla de edades calculada ex post.'),
                        html.Li('La fase 3 aún no está implementada, por lo que no se muestran métricas de cobertura enfermera, skill, workload ni continuidad asistencial.')
                    ]),
                    html.Div([
                        kpi_card('RoomGenderMix', int(room_metrics['occ_df']['gender_mix_violation'].sum()), 'Esperado 0 tras fase 2'),
                        kpi_card('RoomCapacity', int(room_metrics['occ_df']['capacity_violation'].sum()), 'Esperado 0 tras fase 2'),
                        kpi_card('Fase 3', 'Pendiente', 'No implementada todavía'),
                    ], style={'display': 'flex', 'gap': '12px', 'flexWrap': 'wrap', 'marginTop': '12px'})
                ])
            ])
        ])
    ], style={'fontFamily': 'Arial, sans-serif', 'padding': '20px', 'backgroundColor': '#f7f7f9'})

    return app


if __name__ == '__main__':
    app = build_dashboard()
    app.run(debug=True)
