import json
import gurobipy as gp
from gurobipy import GRB


print(">>> Ejecutando modelo_habitaciones_gurobi.py")


def cargar_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def guardar_json(data, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def construir_estancias(instancia, pacientes_planificados):
    pacientes_inst = {p["id"]: p for p in instancia["patients"]}
    admission = {p["id"]: p["admission_day"] for p in pacientes_planificados}

    estancia = {}
    for ps in pacientes_planificados:
        pid = ps["id"]
        d0 = admission[pid]
        los = pacientes_inst[pid]["length_of_stay"]
        estancia[pid] = list(range(d0, min(d0 + los, instancia["days"])))

    return estancia


def generar_feedback(instancia, pacientes_planificados, conflict_days, ruta_feedback, motivo="habitaciones"):
    """
    Genera feedback para la fase de admisión.

    Reduce en 1 el número actual de admisiones de los días conflictivos.
    Como en fase 1A estos límites se tratan como suaves, no destruyen la
    factibilidad, pero orientan al modelo a descongestionar esos días.
    """
    dias = list(range(instancia["days"]))
    pacientes_inst = {p["id"]: p for p in instancia["patients"]}

    admisiones_por_dia = {d: 0 for d in dias}
    admisiones_por_genero_dia = {}

    for ps in pacientes_planificados:
        pid = ps["id"]
        d = ps["admission_day"]
        g = pacientes_inst[pid]["gender"]

        admisiones_por_dia[d] += 1
        admisiones_por_genero_dia.setdefault(g, {dd: 0 for dd in dias})
        admisiones_por_genero_dia[g][d] += 1

    day_caps = {}
    gender_caps = {}
    day_penalties = {}
    gender_penalties = {}

    for d in sorted(conflict_days):
        if d not in dias:
            continue

        current = admisiones_por_dia[d]
        if current > 0:
            day_caps[str(d)] = max(0, current - 1)
            day_penalties[str(d)] = 10.0

        for g, mapa in admisiones_por_genero_dia.items():
            current_g = mapa.get(d, 0)
            if current_g > 0:
                gender_caps.setdefault(g, {})
                gender_penalties.setdefault(g, {})
                gender_caps[g][str(d)] = max(0, current_g - 1)
                gender_penalties[g][str(d)] = 5.0

    feedback = {
        "source": motivo,
        "day_penalties": day_penalties,
        "gender_day_penalties": gender_penalties,
        "day_admission_caps": day_caps,
        "gender_day_admission_caps": gender_caps
    }

    guardar_json(feedback, ruta_feedback)
    print(f"[INFO] Feedback generado en {ruta_feedback}: {feedback}")


def resolver_habitaciones(
    instancia,
    pacientes_planificados,
    time_limit=60,
    mip_gap=0.02,
    optimizar_mezcla_edad=True
):
    """
    Asigna habitaciones a pacientes con día de admisión ya fijado.

    Restricciones:
      - una habitación por paciente,
      - incompatibilidades paciente-habitación,
      - capacidad diaria,
      - no mezcla de género,
      - ocupantes iniciales.

    Objetivo:
      - minimizar RoomAgeMix por habitación y día.
    """
    dias = list(range(instancia["days"]))
    habitaciones = instancia["rooms"]
    pacientes_inst = {p["id"]: p for p in instancia["patients"]}
    ocupantes = instancia.get("occupants", [])
    pesos = instancia["weights"]

    id_pacientes = [p["id"] for p in pacientes_planificados]
    habitacion_ids = [r["id"] for r in habitaciones]
    cap = {r["id"]: r["capacity"] for r in habitaciones}

    if len(id_pacientes) == 0:
        return [], None

    generos = sorted(set(
        [p["gender"] for p in instancia["patients"]] +
        [o["gender"] for o in ocupantes]
    ))

    age_groups = instancia.get("age_groups", [])
    age_value = {ag: i for i, ag in enumerate(age_groups)}
    max_age_value = max(age_value.values()) if age_value else 0
    M_age = max(1, max_age_value)

    estancia = construir_estancias(instancia, pacientes_planificados)

    ocupantes_por_hab = {r: {d: [] for d in dias} for r in habitacion_ids}
    for oc in ocupantes:
        rid = oc["room_id"]
        for d in range(min(oc["length_of_stay"], instancia["days"])):
            if rid in ocupantes_por_hab:
                ocupantes_por_hab[rid][d].append(oc)

    m = gp.Model("Fase1B_Habitaciones")
    m.setParam("OutputFlag", 1)
    m.setParam("TimeLimit", time_limit)
    m.setParam("MIPGap", mip_gap)

    # z[p,r] = 1 si paciente p se asigna a habitación r.
    z = m.addVars(id_pacientes, habitacion_ids, vtype=GRB.BINARY, name="z_room")

    # theta[r,d,g] = 1 si habitación r en día d se dedica al género g.
    theta = m.addVars(habitacion_ids, dias, generos, vtype=GRB.BINARY, name="theta_gender")

    # occ[r,d] = 1 si habitación r está ocupada el día d.
    occ = m.addVars(habitacion_ids, dias, vtype=GRB.BINARY, name="occ_room_day")

    # Variables para RoomAgeMix.
    age_max = m.addVars(habitacion_ids, dias, lb=0, ub=max_age_value, vtype=GRB.CONTINUOUS, name="age_max")
    age_min = m.addVars(habitacion_ids, dias, lb=0, ub=max_age_value, vtype=GRB.CONTINUOUS, name="age_min")

    # 1) Asignación única e incompatibilidades.
    for pid in id_pacientes:
        m.addConstr(z.sum(pid, "*") == 1, name=f"asig_room_{pid}")

        incompatibles = set(pacientes_inst[pid].get("incompatible_room_ids", []))
        for rid in incompatibles:
            if rid in habitacion_ids:
                m.addConstr(z[pid, rid] == 0, name=f"incompatible_{pid}_{rid}")

    # 2) Capacidad y ocupación.
    for rid in habitacion_ids:
        for d in dias:
            presentes = [pid for pid in id_pacientes if d in estancia[pid]]
            fijos = ocupantes_por_hab[rid][d]

            total_presentes = gp.quicksum(z[pid, rid] for pid in presentes) + len(fijos)

            m.addConstr(total_presentes <= cap[rid], name=f"capacidad_{rid}_{d}")

            for pid in presentes:
                m.addConstr(occ[rid, d] >= z[pid, rid], name=f"occ_new_{pid}_{rid}_{d}")

            if len(fijos) > 0:
                m.addConstr(occ[rid, d] == 1, name=f"occ_fixed_{rid}_{d}")

            m.addConstr(total_presentes <= cap[rid] * occ[rid, d], name=f"occ_upper_{rid}_{d}")

    # 3) No mezcla de género.
    for rid in habitacion_ids:
        for d in dias:
            m.addConstr(
                gp.quicksum(theta[rid, d, g] for g in generos) <= 1,
                name=f"one_gender_{rid}_{d}"
            )

            for g in generos:
                nuevos_g = [
                    pid for pid in id_pacientes
                    if d in estancia[pid] and pacientes_inst[pid]["gender"] == g
                ]
                fijos_g = sum(1 for oc in ocupantes_por_hab[rid][d] if oc["gender"] == g)

                m.addConstr(
                    gp.quicksum(z[pid, rid] for pid in nuevos_g) + fijos_g <= cap[rid] * theta[rid, d, g],
                    name=f"gender_{g}_{rid}_{d}"
                )

    # 4) RoomAgeMix.
    for rid in habitacion_ids:
        for d in dias:
            presentes = [pid for pid in id_pacientes if d in estancia[pid]]

            m.addConstr(age_max[rid, d] <= max_age_value * occ[rid, d], name=f"age_max_empty_{rid}_{d}")
            m.addConstr(age_min[rid, d] <= max_age_value * occ[rid, d], name=f"age_min_empty_{rid}_{d}")

            for pid in presentes:
                e = age_value[pacientes_inst[pid]["age_group"]]

                m.addConstr(age_max[rid, d] >= e * z[pid, rid], name=f"age_max_{pid}_{rid}_{d}")
                m.addConstr(age_min[rid, d] <= e + M_age * (1 - z[pid, rid]), name=f"age_min_{pid}_{rid}_{d}")

            for oc in ocupantes_por_hab[rid][d]:
                e = age_value[oc["age_group"]]
                m.addConstr(age_max[rid, d] >= e, name=f"age_max_occ_{oc['id']}_{rid}_{d}")
                m.addConstr(age_min[rid, d] <= e, name=f"age_min_occ_{oc['id']}_{rid}_{d}")

    if optimizar_mezcla_edad:
        obj_age = gp.quicksum(
            pesos["room_mixed_age"] * (age_max[rid, d] - age_min[rid, d])
            for rid in habitacion_ids
            for d in dias
        )

        # Desempate muy pequeño: evita abrir ocupaciones innecesarias.
        obj_balance = gp.quicksum(0.001 * occ[rid, d] for rid in habitacion_ids for d in dias)

        m.setObjective(obj_age + obj_balance, GRB.MINIMIZE)
    else:
        m.setObjective(0.0, GRB.MINIMIZE)

    print("[INFO] Optimizando asignación de habitaciones...")
    m.optimize()

    if m.SolCount == 0:
        print("[WARNING] Modelo de habitaciones infactible.")
        try:
            m.computeIIS()
            m.write("fase_habitaciones_iis.ilp")
        except Exception:
            pass
        return None, m

    room_assignment = {}
    for pid in id_pacientes:
        for rid in habitacion_ids:
            if z[pid, rid].X > 0.5:
                room_assignment[pid] = rid
                break

    salida = []
    for ps in pacientes_planificados:
        nuevo = dict(ps)
        nuevo["room"] = room_assignment[nuevo["id"]]
        salida.append(nuevo)

    return salida, m


def resolver_quirofanos(instancia, pacientes_con_habitacion, time_limit=60, mip_gap=0.02):
    """
    Asigna quirófanos una vez fijados admission_day y room.

    Se resuelve un MILP independiente por día.
    """
    dias = list(range(instancia["days"]))
    pacientes_inst = {p["id"]: p for p in instancia["patients"]}
    quirofanos = instancia["operating_theaters"]
    cirujanos = instancia["surgeons"]
    pesos = instancia["weights"]

    ot_ids = [q["id"] for q in quirofanos]
    cap_ot = {(q["id"], d): q["availability"][d] for q in quirofanos for d in dias}
    cir_ids = [s["id"] for s in cirujanos]

    pacientes_por_dia = {d: [] for d in dias}
    for ps in pacientes_con_habitacion:
        pacientes_por_dia[ps["admission_day"]].append(ps["id"])

    ot_assignment = {}

    for d in dias:
        pids_d = pacientes_por_dia[d]

        if len(pids_d) == 0:
            continue

        m = gp.Model(f"Fase2_Quirofanos_dia_{d}")
        m.setParam("OutputFlag", 0)
        m.setParam("TimeLimit", time_limit)
        m.setParam("MIPGap", mip_gap)

        x = {}
        for pid in pids_d:
            dur = pacientes_inst[pid]["surgery_duration"]
            for ot in ot_ids:
                if cap_ot[ot, d] >= dur:
                    x[pid, ot] = m.addVar(vtype=GRB.BINARY, name=f"x_{pid}_{ot}")

        O = m.addVars(ot_ids, vtype=GRB.BINARY, name="open_ot")
        U = m.addVars(cir_ids, ot_ids, vtype=GRB.BINARY, name="surgeon_uses_ot")
        transfer_extra = m.addVars(cir_ids, lb=0, vtype=GRB.INTEGER, name="transfer_extra")

        m.update()

        # Cada paciente exactamente a un quirófano.
        for pid in pids_d:
            vars_pid = [x[pid, ot] for ot in ot_ids if (pid, ot) in x]
            if len(vars_pid) == 0:
                print(f"[WARNING] Paciente {pid} no cabe en ningún quirófano el día {d}.")
                return None, d

            m.addConstr(gp.quicksum(vars_pid) == 1, name=f"asig_ot_{pid}")

        # Capacidad de quirófano.
        for ot in ot_ids:
            m.addConstr(
                gp.quicksum(
                    pacientes_inst[pid]["surgery_duration"] * x[pid, ot]
                    for pid in pids_d
                    if (pid, ot) in x
                ) <= cap_ot[ot, d] * O[ot],
                name=f"cap_ot_{ot}_{d}"
            )

        # Uso de quirófanos por cirujano y transferencias.
        for sid in cir_ids:
            for ot in ot_ids:
                expr = gp.quicksum(
                    x[pid, ot]
                    for pid in pids_d
                    if pacientes_inst[pid]["surgeon_id"] == sid and (pid, ot) in x
                )

                m.addConstr(expr <= len(pids_d) * U[sid, ot], name=f"use_lb_{sid}_{ot}_{d}")
                m.addConstr(U[sid, ot] <= expr, name=f"use_ub_{sid}_{ot}_{d}")

            m.addConstr(
                transfer_extra[sid] >= gp.quicksum(U[sid, ot] for ot in ot_ids) - 1,
                name=f"transfer_extra_{sid}_{d}"
            )

        obj = (
            gp.quicksum(pesos["open_operating_theater"] * O[ot] for ot in ot_ids)
            + gp.quicksum(pesos["surgeon_transfer"] * transfer_extra[sid] for sid in cir_ids)
        )

        m.setObjective(obj, GRB.MINIMIZE)
        m.optimize()

        if m.SolCount == 0:
            print(f"[WARNING] Asignación de quirófanos infactible en día {d}.")
            try:
                m.computeIIS()
                m.write(f"fase_quirofanos_dia_{d}_iis.ilp")
            except Exception:
                pass
            return None, d

        for pid in pids_d:
            for ot in ot_ids:
                if (pid, ot) in x and x[pid, ot].X > 0.5:
                    ot_assignment[pid] = ot
                    break

    salida = []
    for ps in pacientes_con_habitacion:
        nuevo = dict(ps)
        nuevo["operating_theater"] = ot_assignment.get(nuevo["id"])
        salida.append(nuevo)

    return salida, None


def resolver_habitaciones_debug(
    ruta_instancia,
    ruta_sol_previa,
    ruta_final="solucion_fase2.json",
    ruta_feedback="feedback_fase2.json",
    time_limit=60
):
    """
    Nueva fase 2 compatible con el notebook anterior.

    Hace dos pasos:
      1) habitaciones,
      2) quirófanos.

    Entrada:
      solucion_fase1.json con id y admission_day. Puede venir sin quirófano.

    Salida:
      solucion_fase2.json con id, admission_day, room y operating_theater.
    """

    print(">>> Entrando en resolver_habitaciones_debug")
    print("--- FASE 1B + FASE 2: habitaciones y quirófanos ---")

    instancia = cargar_json(ruta_instancia)
    sol_previa = cargar_json(ruta_sol_previa)

    pacientes_planificados = sol_previa.get("patients", [])
    print(f">>> Pacientes planificados: {len(pacientes_planificados)}")

    if len(pacientes_planificados) == 0:
        guardar_json({"patients": [], "nurses": []}, ruta_final)
        guardar_json({
            "day_penalties": {},
            "gender_day_penalties": {},
            "day_admission_caps": {},
            "gender_day_admission_caps": {}
        }, ruta_feedback)
        return

    # 1) Habitaciones.
    pacientes_con_habitacion, modelo_hab = resolver_habitaciones(
        instancia,
        pacientes_planificados,
        time_limit=time_limit,
        mip_gap=0.02,
        optimizar_mezcla_edad=True
    )

    if pacientes_con_habitacion is None:
        dias = list(range(instancia["days"]))
        conflict_days = set()

        if modelo_hab is not None:
            for c in modelo_hab.getConstrs():
                if getattr(c, "IISConstr", 0):
                    name = c.ConstrName
                    for d in dias:
                        if name.endswith(f"_{d}") or f"_{d}_" in name:
                            conflict_days.add(d)

        if not conflict_days:
            counts = {}
            for ps in pacientes_planificados:
                counts[ps["admission_day"]] = counts.get(ps["admission_day"], 0) + 1

            if counts:
                max_count = max(counts.values())
                conflict_days = {d for d, c in counts.items() if c == max_count}

        print(f"[INFO] Días conflictivos habitaciones: {sorted(conflict_days)}")
        generar_feedback(instancia, pacientes_planificados, conflict_days, ruta_feedback, motivo="habitaciones")
        return

    # 2) Quirófanos.
    pacientes_completos, dia_conflictivo_ot = resolver_quirofanos(
        instancia,
        pacientes_con_habitacion,
        time_limit=time_limit,
        mip_gap=0.02
    )

    if pacientes_completos is None:
        generar_feedback(instancia, pacientes_con_habitacion, {dia_conflictivo_ot}, ruta_feedback, motivo="quirofanos")
        return

    solucion_final = {"patients": pacientes_completos, "nurses": []}
    guardar_json(solucion_final, ruta_final)

    guardar_json({
        "day_penalties": {},
        "gender_day_penalties": {},
        "day_admission_caps": {},
        "gender_day_admission_caps": {}
    }, ruta_feedback)

    print(f"[INFO] Solución completa fase 2 guardada en: {ruta_final}")
    print("[INFO] Feedback vacío generado.")


if __name__ == "__main__":
    resolver_habitaciones_debug(
        ruta_instancia="test01.json",
        ruta_sol_previa="solucion_fase1.json",
        ruta_final="solucion_fase2.json",
        ruta_feedback="feedback_fase2.json",
        time_limit=60
    )
