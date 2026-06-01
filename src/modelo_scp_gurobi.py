import json
import itertools
import gurobipy as gp
from gurobipy import GRB


def cargar_instancia(ruta_archivo):
    with open(ruta_archivo, "r", encoding="utf-8") as f:
        return json.load(f)


def cargar_feedback(ruta_feedback=None):
    """
    Carga información de realimentación generada por la fase de habitaciones
    o por la fase de quirófanos.
    """
    feedback_vacio = {
        "day_penalties": {},
        "gender_day_penalties": {},
        "day_admission_caps": {},
        "gender_day_admission_caps": {}
    }

    if ruta_feedback is None:
        return feedback_vacio

    try:
        with open(ruta_feedback, "r", encoding="utf-8") as f:
            data = json.load(f)

        for k in feedback_vacio:
            if k not in data:
                data[k] = {}

        return data

    except FileNotFoundError:
        print(f"[INFO] No se encontró {ruta_feedback}. Se ejecuta sin feedback.")
        return feedback_vacio


def habitaciones_compatibles_paciente(paciente, habitacion_ids):
    incompatibles = set(paciente.get("incompatible_room_ids", []))
    return [r for r in habitacion_ids if r not in incompatibles]


def construir_ocupacion_fija(instancia, habitacion_ids, dias):
    ocupantes = instancia.get("occupants", [])

    generos = sorted(set(
        [p["gender"] for p in instancia["patients"]] +
        [o["gender"] for o in ocupantes]
    ))

    ocupantes_por_hab_dia = {r: {d: [] for d in dias} for r in habitacion_ids}
    camas_ocupadas_fijas = {d: 0 for d in dias}
    camas_ocupadas_fijas_genero = {g: {d: 0 for d in dias} for g in generos}

    for oc in ocupantes:
        rid = oc["room_id"]
        g = oc["gender"]

        for d in range(min(oc["length_of_stay"], instancia["days"])):
            if rid in ocupantes_por_hab_dia:
                ocupantes_por_hab_dia[rid][d].append(oc)

            camas_ocupadas_fijas[d] += 1
            camas_ocupadas_fijas_genero[g][d] += 1

    return ocupantes_por_hab_dia, camas_ocupadas_fijas, camas_ocupadas_fijas_genero


def existe_quirofano_factible_para_paciente_en_dia(paciente, quirofanos, d):
    dur = paciente["surgery_duration"]
    return any(q["availability"][d] >= dur for q in quirofanos)


def subconjuntos_no_vacios(lista):
    for r in range(1, len(lista) + 1):
        for comb in itertools.combinations(lista, r):
            yield set(comb)


def resolver_fase1_scp_interactiva(
    instancia,
    ruta_salida="solucion_fase1.json",
    ruta_feedback=None,
    time_limit=120,
    mip_gap=0.02,
    usar_capacidad_genero=True,
    usar_hall_compatibilidad=True,
    usar_hall_quirofanos=True,
    feedback_caps_suaves=True,
    penalizacion_slack_feedback=1000.0,
    penalizacion_admisiones_dias_conflictivos=1.0,
    priorizar_opcionales=True,
    salida_estadisticas=None
):
    """
    Nueva fase 1: admisión de pacientes.

    Esta fase decide únicamente:
      - qué pacientes se programan;
      - en qué día son admitidos.

    La asignación concreta de quirófanos se pospone hasta después de la
    asignación de habitaciones. Se mantienen restricciones quirúrgicas
    agregadas para no generar admisiones imposibles.
    """

    print("\n--- FASE 1A: Admisión de pacientes + anticipación de camas y quirófanos ---")

    dias = list(range(instancia["days"]))
    pacientes = instancia["patients"]
    habitaciones = instancia["rooms"]
    quirofanos = instancia["operating_theaters"]
    cirujanos = instancia["surgeons"]
    ocupantes = instancia.get("occupants", [])
    pesos = instancia["weights"]

    feedback = cargar_feedback(ruta_feedback)

    habitacion_ids = [r["id"] for r in habitaciones]
    cap_room = {r["id"]: r["capacity"] for r in habitaciones}
    total_camas = sum(cap_room.values())

    ot_ids = [q["id"] for q in quirofanos]
    cap_ot = {(q["id"], d): q["availability"][d] for q in quirofanos for d in dias}

    generos = sorted(set(
        [p["gender"] for p in pacientes] +
        [o["gender"] for o in ocupantes]
    ))

    (
        ocupantes_por_hab_dia,
        camas_ocupadas_fijas,
        camas_ocupadas_fijas_genero
    ) = construir_ocupacion_fija(instancia, habitacion_ids, dias)

    # Feedback
    day_penalties = {
        int(d): float(v) for d, v in feedback.get("day_penalties", {}).items()
    }

    gender_day_penalties = {}
    for g, mapa in feedback.get("gender_day_penalties", {}).items():
        gender_day_penalties[g] = {int(d): float(v) for d, v in mapa.items()}

    day_admission_caps = {
        int(d): int(v) for d, v in feedback.get("day_admission_caps", {}).items()
    }

    gender_day_admission_caps = {}
    for g, mapa in feedback.get("gender_day_admission_caps", {}).items():
        gender_day_admission_caps[g] = {int(d): int(v) for d, v in mapa.items()}

    m = gp.Model("Fase1A_Admisiones")
    m.setParam("OutputFlag", 1)
    m.setParam("TimeLimit", time_limit)
    m.setParam("MIPGap", mip_gap)

    # a[p,d] = 1 si paciente p se admite el día d.
    a = {}

    for p in pacientes:
        pid = p["id"]
        release = p["surgery_release_day"]
        due = p.get("surgery_due_day", instancia["days"] - 1)
        compatibles = habitaciones_compatibles_paciente(p, habitacion_ids)

        for d in dias:
            if not (release <= d <= due):
                continue

            # No se crea variable si la cirugía no cabe en ningún quirófano ese día
            # o si el paciente no tiene ninguna habitación compatible.
            if not existe_quirofano_factible_para_paciente_en_dia(p, quirofanos, d):
                continue

            if len(compatibles) == 0:
                continue

            a[pid, d] = m.addVar(vtype=GRB.BINARY, name=f"a_{pid}_{d}")

    opcionales = [p["id"] for p in pacientes if not p.get("mandatory", False)]
    u = m.addVars(opcionales, vtype=GRB.BINARY, name="unscheduled")

    # Proxy de quirófanos abiertos: estima capacidad diaria activada, sin asignar
    # pacientes a quirófanos concretos.
    open_proxy = m.addVars(ot_ids, dias, vtype=GRB.BINARY, name="open_ot_proxy")

    # Partición aproximada de habitaciones por género.
    theta = {}
    if usar_capacidad_genero:
        theta = m.addVars(habitacion_ids, dias, generos, vtype=GRB.BINARY, name="theta_gender_room")

    # Slacks para feedback.
    slack_day_cap = {}
    slack_gender_day_cap = {}

    if feedback_caps_suaves:
        for d in day_admission_caps:
            if d in dias:
                slack_day_cap[d] = m.addVar(lb=0, vtype=GRB.CONTINUOUS, name=f"slack_feedback_day_{d}")

        for g, mapa in gender_day_admission_caps.items():
            for d in mapa:
                if d in dias:
                    slack_gender_day_cap[g, d] = m.addVar(lb=0, vtype=GRB.CONTINUOUS, name=f"slack_feedback_gender_{g}_{d}")

    m.update()

    # 1) Admisión obligatoria/opcional.
    for p in pacientes:
        pid = p["id"]
        expr_adm = gp.quicksum(a[pid, d] for d in dias if (pid, d) in a)

        if p.get("mandatory", False):
            m.addConstr(expr_adm == 1, name=f"admision_obligatoria_{pid}")
        else:
            m.addConstr(expr_adm + u[pid] == 1, name=f"admision_opcional_{pid}")

    # 2) Capacidad total diaria de quirófanos activados.
    for d in dias:
        total_minutes_d = gp.quicksum(
            p["surgery_duration"] * a[p["id"], d]
            for p in pacientes
            if (p["id"], d) in a
        )

        activated_capacity_d = gp.quicksum(
            cap_ot[ot, d] * open_proxy[ot, d]
            for ot in ot_ids
        )

        m.addConstr(total_minutes_d <= activated_capacity_d, name=f"capacidad_ot_agregada_{d}")

        for ot in ot_ids:
            if cap_ot[ot, d] <= 0:
                m.addConstr(open_proxy[ot, d] == 0, name=f"ot_sin_capacidad_{ot}_{d}")

    # 3) Capacidad diaria por cirujano.
    for s in cirujanos:
        sid = s["id"]
        for d in dias:
            max_t = s["max_surgery_time"][d]
            m.addConstr(
                gp.quicksum(
                    p["surgery_duration"] * a[p["id"], d]
                    for p in pacientes
                    if p["surgeon_id"] == sid and (p["id"], d) in a
                ) <= max_t,
                name=f"capacidad_cirujano_{sid}_{d}"
            )

    # 4) Hall para quirófanos: evita algunos fallos de empaquetamiento.
    if usar_hall_quirofanos:
        for d in dias:
            for subset in subconjuntos_no_vacios(ot_ids):
                if len(subset) == len(ot_ids):
                    continue

                outside = set(ot_ids) - subset
                cap_subset = sum(cap_ot[ot, d] for ot in subset)

                forced_expr = gp.quicksum(
                    p["surgery_duration"] * a[p["id"], d]
                    for p in pacientes
                    if (p["id"], d) in a
                    and all(cap_ot[ot, d] < p["surgery_duration"] for ot in outside)
                )

                subset_name = "_".join(sorted(subset))
                m.addConstr(forced_expr <= cap_subset, name=f"hall_ot_{subset_name}_{d}")

    # 5) Capacidad total de camas.
    for d in dias:
        ocupacion_dinamica = gp.quicksum(
            a[p["id"], d0]
            for p in pacientes
            for d0 in dias
            if (p["id"], d0) in a
            and d0 <= d < min(d0 + p["length_of_stay"], instancia["days"])
        )

        m.addConstr(
            ocupacion_dinamica + camas_ocupadas_fijas[d] <= total_camas,
            name=f"capacidad_camas_total_{d}"
        )

    # 6) Capacidad por género mediante partición aproximada de habitaciones.
    if usar_capacidad_genero:
        for rid in habitacion_ids:
            for d in dias:
                m.addConstr(
                    gp.quicksum(theta[rid, d, g] for g in generos) <= 1,
                    name=f"one_gender_room_{rid}_{d}"
                )

                fixed_genders = sorted(set(oc["gender"] for oc in ocupantes_por_hab_dia[rid][d]))
                for g in fixed_genders:
                    m.addConstr(theta[rid, d, g] == 1, name=f"fixed_gender_{rid}_{d}_{g}")

        for g in generos:
            for d in dias:
                ocupacion_genero = gp.quicksum(
                    a[p["id"], d0]
                    for p in pacientes
                    for d0 in dias
                    if (p["id"], d0) in a
                    and p["gender"] == g
                    and d0 <= d < min(d0 + p["length_of_stay"], instancia["days"])
                )

                capacidad_genero = gp.quicksum(
                    cap_room[rid] * theta[rid, d, g]
                    for rid in habitacion_ids
                )

                m.addConstr(
                    ocupacion_genero + camas_ocupadas_fijas_genero[g][d] <= capacidad_genero,
                    name=f"capacidad_camas_genero_{g}_{d}"
                )

    # 7) Hall para compatibilidad paciente-habitación.
    if usar_hall_compatibilidad:
        room_subsets = list(subconjuntos_no_vacios(habitacion_ids))
        compatible_set = {
            p["id"]: set(habitaciones_compatibles_paciente(p, habitacion_ids))
            for p in pacientes
        }

        for d in dias:
            for subset in room_subsets:
                if len(subset) == len(habitacion_ids):
                    continue

                cap_subset = sum(cap_room[r] for r in subset)
                fixed_subset = sum(
                    1
                    for r in subset
                    for oc in ocupantes_por_hab_dia[r][d]
                )

                forced_expr = gp.quicksum(
                    a[p["id"], d0]
                    for p in pacientes
                    for d0 in dias
                    if (p["id"], d0) in a
                    and compatible_set[p["id"]].issubset(subset)
                    and d0 <= d < min(d0 + p["length_of_stay"], instancia["days"])
                )

                subset_name = "_".join(sorted(subset))
                m.addConstr(forced_expr + fixed_subset <= cap_subset, name=f"hall_rooms_{subset_name}_{d}")

    # 8) Feedback de habitaciones/quirófanos.
    for d, cap_d in day_admission_caps.items():
        if d in dias:
            expr_adm_d = gp.quicksum(a[p["id"], d] for p in pacientes if (p["id"], d) in a)

            if feedback_caps_suaves:
                m.addConstr(expr_adm_d <= cap_d + slack_day_cap[d], name=f"feedback_cap_total_suave_{d}")
            else:
                m.addConstr(expr_adm_d <= cap_d, name=f"feedback_cap_total_duro_{d}")

    for g, mapa in gender_day_admission_caps.items():
        for d, cap_gd in mapa.items():
            if d in dias:
                expr_adm_gd = gp.quicksum(
                    a[p["id"], d]
                    for p in pacientes
                    if p["gender"] == g and (p["id"], d) in a
                )

                if feedback_caps_suaves:
                    m.addConstr(expr_adm_gd <= cap_gd + slack_gender_day_cap[g, d],
                                name=f"feedback_cap_genero_suave_{g}_{d}")
                else:
                    m.addConstr(expr_adm_gd <= cap_gd, name=f"feedback_cap_genero_duro_{g}_{d}")

    # Objetivo.
    coste_unscheduled = gp.quicksum(pesos["unscheduled_optional"] * u[pid] for pid in opcionales)

    coste_delay = gp.quicksum(
        pesos["patient_delay"] * (d - p["surgery_release_day"]) * a[p["id"], d]
        for p in pacientes
        for d in dias
        if (p["id"], d) in a
    )

    coste_open_proxy = gp.quicksum(
        pesos["open_operating_theater"] * open_proxy[ot, d]
        for ot in ot_ids
        for d in dias
    )

    coste_feedback = gp.quicksum(
        penalizacion_admisiones_dias_conflictivos * day_penalties.get(d, 0.0) * a[p["id"], d]
        for p in pacientes
        for d in dias
        if (p["id"], d) in a
    ) + gp.quicksum(
        penalizacion_admisiones_dias_conflictivos *
        gender_day_penalties.get(p["gender"], {}).get(d, 0.0) *
        a[p["id"], d]
        for p in pacientes
        for d in dias
        if (p["id"], d) in a
    )

    coste_slacks = gp.quicksum(
        penalizacion_slack_feedback * slack_day_cap[d]
        for d in slack_day_cap
    ) + gp.quicksum(
        penalizacion_slack_feedback * slack_gender_day_cap[g, d]
        for (g, d) in slack_gender_day_cap
    )

    coste_secundario = gp.quicksum(
        0.001 * d * a[p["id"], d]
        for p in pacientes
        for d in dias
        if (p["id"], d) in a
    )

    if priorizar_opcionales:
        m.ModelSense = GRB.MINIMIZE
        m.setObjectiveN(coste_unscheduled, index=0, priority=4, name="unscheduled_optional")
        m.setObjectiveN(coste_slacks, index=1, priority=3, name="feedback_slacks")
        m.setObjectiveN(coste_delay + coste_feedback, index=2, priority=2, name="delay_feedback")
        m.setObjectiveN(coste_open_proxy + coste_secundario, index=3, priority=1, name="open_ot_proxy")
    else:
        m.setObjective(
            coste_unscheduled + coste_delay + coste_open_proxy + coste_feedback + coste_slacks + coste_secundario,
            GRB.MINIMIZE
        )

    print("Optimizando fase 1A...")
    m.optimize()

    if m.SolCount == 0:
        print("[ERROR] No se encontró ninguna solución factible para fase 1A.")
        return None

    solucion_pacientes = []
    for p in pacientes:
        pid = p["id"]
        dia_asignado = None

        for d in dias:
            if (pid, d) in a and a[pid, d].X > 0.5:
                dia_asignado = d
                break

        if dia_asignado is None:
            continue

        solucion_pacientes.append({
            "id": pid,
            "admission_day": dia_asignado,
            "room": None,
            "operating_theater": None
        })

    solucion_final = {"patients": solucion_pacientes, "nurses": []}

    with open(ruta_salida, "w", encoding="utf-8") as f:
        json.dump(solucion_final, f, indent=4)

    unscheduled_optional = sum(1 for pid in opcionales if u[pid].X > 0.5)

    stats = {
        "status": int(m.Status),
        "scheduled_patients": len(solucion_pacientes),
        "unscheduled_optional": int(unscheduled_optional),
        "open_operating_theater_proxy": int(round(sum(open_proxy[ot, d].X for ot in ot_ids for d in dias))),
        "feedback_slack_total": float(
            sum(slack_day_cap[d].X for d in slack_day_cap) +
            sum(slack_gender_day_cap[g, d].X for (g, d) in slack_gender_day_cap)
        ) if feedback_caps_suaves else 0.0
    }

    print("[INFO] Solución fase 1A guardada en:", ruta_salida)
    print("[INFO] Resumen fase 1A:")
    for k, v in stats.items():
        print(f"  - {k}: {v}")

    if salida_estadisticas is not None:
        with open(salida_estadisticas, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=4)

    return solucion_final


if __name__ == "__main__":
    instancia = cargar_instancia("test01.json")

    resolver_fase1_scp_interactiva(
        instancia,
        ruta_salida="solucion_fase1.json",
        ruta_feedback="feedback_fase2.json",
        time_limit=120,
        mip_gap=0.02,
        usar_capacidad_genero=True,
        usar_hall_compatibilidad=True,
        usar_hall_quirofanos=True,
        feedback_caps_suaves=True,
        penalizacion_slack_feedback=1000.0,
        penalizacion_admisiones_dias_conflictivos=1.0,
        priorizar_opcionales=True,
        salida_estadisticas="estadisticas_fase1.json"
    )
