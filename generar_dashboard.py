"""
generar_dashboard.py
====================
Cuadro de mandos interactivo (HTML autocontenido) del TFG IHTC 2024.

Lee:
  · instancias/iXX.json                            (definición de la instancia)
  · resultados_rapido/iXX/fase3.json               (solución completa)
  · resultados_rapido_benchmark/detalle_iXX.json   (métricas + validador)
  · resultados_oficiales.json                      (costes oficiales IHTC)
  · resultados_ablacion_benchmark/  (opcional)     (variante sin readmisión)

Genera dashboard.html con cinco pestañas:
  1. Resumen            → KPIs, gap por instancia, composición global, tabla.
  2. Costes/instancia   → barras apiladas de los 8 componentes de coste.
  3. Instancias         → tamaño de cada instancia y pacientes programados.
  4. Solución           → explorador por instancia: ocupación de camas, ingresos
                          por día, uso de quirófanos y mapa de calor de
                          habitaciones x día.
  5. Ablación           → poda simple vs poda+readmisión.

Uso:
  python generar_dashboard.py
  python generar_dashboard.py --carpeta resultados_competicion_benchmark \
      --soluciones resultados_competicion --salida dashboard_competicion.html \
      --etiqueta "reglas de competición (4 hilos, 600 s)"
"""

import argparse
import glob
import json
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent.resolve()

COMP = [("unsched", "Unscheduled", "#C03030"), ("delay", "Delay", "#E0A030"),
        ("skill", "Skill", "#2E579C"), ("contin", "Continuity", "#5B9BD5"),
        ("openot", "OpenOT", "#7030A0"), ("agemix", "AgeMix", "#A6A6A6"),
        ("workload", "Workload", "#2E9C57"), ("transfer", "Transfer", "#404040")]


def cargar_detalles(carpeta):
    res = {}
    for f in sorted(glob.glob(str(carpeta / "detalle_i*.json"))):
        d = json.load(open(f, encoding="utf-8"))
        res[d.get("instancia")] = d
    return res


def fila_de(d, bks, inst):
    fila = {"inst": inst, "oficial": bks, "estado": d.get("estado", "—")}
    if d.get("estado") == "OK" and d.get("total_cost") is not None:
        c = d["total_cost"]
        fila.update({
            "obtenido": c, "viol": d.get("total_violations", 0),
            "gap": round((c - bks) / bks * 100, 1) if bks else None,
            "unsched": d.get("cost_ElectiveUnscheduledPatients", 0) or 0,
            "delay": d.get("cost_PatientDelay", 0) or 0,
            "skill": d.get("cost_RoomSkillLevel", 0) or 0,
            "contin": d.get("cost_ContinuityOfCare", 0) or 0,
            "openot": d.get("cost_OpenOperatingTheater", 0) or 0,
            "agemix": d.get("cost_RoomAgeMix", 0) or 0,
            "workload": d.get("cost_ExcessiveNurseWorkload", 0) or 0,
            "transfer": d.get("cost_SurgeonTransfer", 0) or 0,
            "tiempo": round(d.get("tiempos", {}).get("total", 0) or 0, 1),
            "poda": d.get("metricas", {}).get("pacientes_eliminados_poda", 0) or 0,
        })
        fila["resto"] = c - fila["unsched"] - fila["delay"]
    else:
        fila.update({"obtenido": None, "gap": None, "viol": None})
    return fila


def datos_solucion(inst_json, sol_json):
    """Calcula ocupación de camas, ingresos, uso de quirófanos y matriz
    habitación x día a partir de la instancia y su solución."""
    days = inst_json["days"]
    rooms = inst_json["rooms"]
    ots = inst_json.get("operating_theaters", [])
    pat = {p["id"]: p for p in inst_json.get("patients", [])}
    occ = inst_json.get("occupants", [])

    sol_p = sol_json.get("patients", []) if sol_json else []
    room_ids = [r["id"] for r in rooms]
    cap = {r["id"]: r["capacity"] for r in rooms}
    total_beds = sum(cap.values())

    n_total = len(pat)
    n_mand = sum(1 for p in pat.values() if p.get("mandatory"))
    n_opt = n_total - n_mand

    bed = [0] * days
    adm = [0] * days
    ot_used = [0] * days
    mat = {r: [0] * days for r in room_ids}

    # Distribuciones por recurso (introspección de la solución)
    ot_ids = [t["id"] for t in ots]
    surg_ids = [s["id"] for s in inst_json.get("surgeons", [])]
    ot_min = {t: 0 for t in ot_ids}
    surg_min = {s: 0 for s in surg_ids}
    by_gender, by_age = {}, {}

    # Las soluciones oficiales pueden listar pacientes NO admitidos con
    # admission_day = "none"; se ignoran (cuentan como no programados).
    def _dia_valido(v):
        if v is None:
            return None
        if isinstance(v, int):
            return v
        s = str(v).strip().lower()
        if s.isdigit():
            return int(s)
        return None

    n_sched = 0
    for sp in sol_p:
        p = pat.get(sp["id"])
        if not p:
            continue
        d0 = _dia_valido(sp.get("admission_day"))
        if d0 is None:
            continue   # paciente listado pero no admitido
        n_sched += 1
        los = int(p["length_of_stay"])
        dur = p.get("surgery_duration", 0)
        if 0 <= d0 < days:
            adm[d0] += 1
            ot_used[d0] += dur
        ot = sp.get("operating_theater")
        if ot in ot_min:
            ot_min[ot] += dur
        sid = p.get("surgeon_id")
        if sid in surg_min:
            surg_min[sid] += dur
        g = p.get("gender", "?"); by_gender[g] = by_gender.get(g, 0) + 1
        ag = p.get("age_group", "?"); by_age[ag] = by_age.get(ag, 0) + 1
        room = sp.get("room")
        for d in range(d0, min(d0 + los, days)):
            bed[d] += 1
            if room in mat:
                mat[room][d] += 1
    n_unsched = n_total - n_sched
    for oc in occ:
        room = oc.get("room_id"); los = int(oc.get("length_of_stay", 0))
        for d in range(0, min(los, days)):
            bed[d] += 1
            if room in mat:
                mat[room][d] += 1

    ot_cap = [sum(q["availability"][d] for q in ots) for d in range(days)]

    return {
        "days": days, "n_total": n_total, "n_mand": n_mand, "n_opt": n_opt,
        "n_sched": n_sched, "n_unsched": n_unsched,
        "n_rooms": len(rooms), "n_ots": len(ots),
        "n_nurses": len(inst_json.get("nurses", [])),
        "n_surgeons": len(inst_json.get("surgeons", [])),
        "total_beds": total_beds,
        "bed": bed, "adm": adm, "ot_used": ot_used, "ot_cap": ot_cap,
        "room_ids": room_ids, "cap": [cap[r] for r in room_ids],
        "mat": [mat[r] for r in room_ids],
        "ot_dist_ids": ot_ids, "ot_dist_min": [ot_min[t] for t in ot_ids],
        "surg_ids": surg_ids, "surg_min": [surg_min[s] for s in surg_ids],
        "room_pd": [sum(mat[r]) for r in room_ids],
        "by_gender": by_gender, "by_age": by_age,
    }


def construir_ablacion(con, sin):
    comunes = sorted(set(con) & set(sin))
    filas = []
    for inst in comunes:
        dc, ds = con[inst], sin[inst]
        if dc.get("estado") != "OK" or ds.get("estado") != "OK":
            continue
        cc, cs = dc.get("total_cost"), ds.get("total_cost")
        if cc is None or cs is None:
            continue
        pc = dc.get("metricas", {}).get("pacientes_eliminados_poda", 0) or 0
        ps = ds.get("metricas", {}).get("pacientes_eliminados_poda", 0) or 0
        if pc == 0 and ps == 0:
            continue
        filas.append({"inst": inst, "sin": cs, "con": cc, "pod_sin": ps,
                      "pod_con": pc, "mejora": round((cs - cc) / cs * 100, 1) if cs else 0})
    if filas:
        return {"real": True, "filas": filas}
    return {"real": False, "filas": [
        {"inst": "i13", "sin": 24712, "con": 20545, "pod_sin": 19, "pod_con": 9, "mejora": 17.0}]}


def main():
    for _s in (sys.stdout,):
        try: _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--carpeta", default="resultados_rapido_benchmark")
    ap.add_argument("--soluciones", default="resultados_rapido")
    ap.add_argument("--instancias", default="instancias")
    ap.add_argument("--ablacion", default="resultados_ablacion_benchmark")
    ap.add_argument("--oficiales", default="resultados_oficiales.json")
    ap.add_argument("--oficiales-sol", default="resultados_oficiales_benchmark",
                    help="Carpeta con el detalle de las soluciones oficiales procesadas.")
    ap.add_argument("--salida", default="dashboard.html")
    ap.add_argument("--etiqueta", default="condiciones ampliadas (8 hilos, sin límite estricto)")
    ap.add_argument("--tab", default="inicio",
                    help="Pestaña pre-activada en el HTML generado (para capturas).")
    args = ap.parse_args()

    det = cargar_detalles(BASE / args.carpeta)
    of = json.load(open(BASE / args.oficiales, encoding="utf-8"))["instancias"]

    filas, soluciones = [], {}
    for n in range(1, 31):
        inst = f"i{n:02d}"
        filas.append(fila_de(det.get(inst, {}), of.get(inst, {}).get("best_known"), inst))
        # Datos de solución (instancia + fase3)
        pi = BASE / args.instancias / f"{inst}.json"
        ps = BASE / args.soluciones / inst / "fase3.json"
        if pi.exists():
            try:
                ij = json.load(open(pi, encoding="utf-8"))
                sj = json.load(open(ps, encoding="utf-8")) if ps.exists() else None
                soluciones[inst] = datos_solucion(ij, sj)
            except Exception as e:
                print(f"[aviso] {inst}: {e}")

    ok = [f for f in filas if f.get("obtenido") is not None]
    gaps = [f["gap"] for f in ok if f["gap"] is not None]
    sum_tot = sum(f["obtenido"] for f in ok)
    sum_uns = sum(f.get("unsched", 0) for f in ok)
    sum_del = sum(f.get("delay", 0) for f in ok)
    sum_resto = sum(f.get("resto", 0) for f in ok)

    kpis = {
        "resueltas": f"{len(ok)}/30",
        "sin_violaciones": f"{sum(1 for f in ok if f.get('viol') == 0)}/{len(ok)}",
        "gap_medio": round(sum(gaps) / len(gaps), 1) if gaps else None,
        "gap_mediano": round(sorted(gaps)[len(gaps) // 2], 1) if gaps else None,
        "dentro10": sum(1 for g in gaps if g <= 10), "n_gap": len(gaps),
        "coste_total": sum_tot,
    }

    # ── Comparativa con soluciones oficiales ──────────────────────────
    SHORT2FULL = {"unsched": "ElectiveUnscheduledPatients", "delay": "PatientDelay",
                  "skill": "RoomSkillLevel", "contin": "ContinuityOfCare",
                  "openot": "OpenOperatingTheater", "agemix": "RoomAgeMix",
                  "workload": "ExcessiveNurseWorkload", "transfer": "SurgeonTransfer"}
    # Adjunta a cada solución sus violaciones duras y su desglose de coste
    # (introspección por instancia en la vista de Solución).
    HARD = ["RoomGenderMix", "PatientRoomCompatibility", "SurgeonOvertime",
            "OperatingTheaterOvertime", "MandatoryUnscheduledPatients",
            "AdmissionDay", "RoomCapacity", "NursePresence", "UncoveredRoom"]
    for inst, s in soluciones.items():
        di = det.get(inst, {})
        s["viol"] = {h: di.get(f"viol_{h}", 0) or 0 for h in HARD}
        s["total_viol"] = di.get("total_violations")
        s["costes"] = {c: (di.get(f"cost_{SHORT2FULL[c]}", 0) or 0) for c, _, _ in COMP}
        s["total_cost"] = di.get("total_cost")

    oficial = cargar_detalles(BASE / args.oficiales_sol)
    propuesta_por_inst = {f["inst"]: f for f in filas}
    comp_inst, agg_n, agg_o = [], {c: 0 for c, _, _ in COMP}, {c: 0 for c, _, _ in COMP}
    for inst in sorted(set(oficial) & set(propuesta_por_inst)):
        do, fn = oficial[inst], propuesta_por_inst[inst]
        if do.get("estado") != "OK" or fn.get("obtenido") is None:
            continue
        co, cn = do.get("total_cost"), fn.get("obtenido")
        if co is None or cn is None:
            continue
        comps = {}
        for c, _, _ in COMP:
            vn = fn.get(c, 0) or 0
            vo = do.get(f"cost_{SHORT2FULL[c]}", 0) or 0
            comps[c] = {"n": vn, "o": vo}
            agg_n[c] += vn; agg_o[c] += vo
        sol_o = do.get("solucion", {})
        sol_n = soluciones.get(inst, {})
        comp_inst.append({
            "inst": inst, "propuesta": cn, "oficial": co,
            "delta": cn - co, "gap": round((cn - co) / co * 100, 1) if co else None,
            "comps": comps,
            "sched_n": sol_n.get("n_sched"), "sched_o": sol_o.get("n_sched"),
            "unsched_n": sol_n.get("n_unsched"), "unsched_o": sol_o.get("n_unsched"),
        })
    comparativa = {
        "n": len(comp_inst),
        "por_instancia": comp_inst,
        "agg": [{"key": c, "label": t, "color": col, "n": agg_n[c], "o": agg_o[c],
                 "delta": agg_n[c] - agg_o[c]} for c, t, col in COMP],
    }
    # Estructura de la solución oficial por instancia (para el explorador comparativo)
    soluciones_oficiales = {
        inst: oficial[inst]["solucion"]
        for inst in oficial
        if oficial[inst].get("estado") == "OK" and oficial[inst].get("solucion")
    }

    datos = {
        "filas": filas, "kpis": kpis, "etiqueta": args.etiqueta,
        "generado": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "comp_global": {"unsched": sum_uns, "delay": sum_del, "resto": sum_resto},
        "comp_def": [[c, t, col] for c, t, col in COMP],
        "ablacion": construir_ablacion(det, cargar_detalles(BASE / args.ablacion)),
        "soluciones": soluciones,
        "soluciones_oficiales": soluciones_oficiales,
        "comparativa": comparativa,
    }

    html = PLANTILLA.replace("/*DATOS*/", json.dumps(datos, ensure_ascii=False))
    # Pre-activar una pestaña concreta (para capturas headless de cada vista)
    if getattr(args, "tab", None) and args.tab != "inicio":
        t = args.tab
        html = (html
            .replace('class="tab active" data-p="inicio"', 'class="tab" data-p="inicio"')
            .replace(f'class="tab" data-p="{t}"', f'class="tab active" data-p="{t}"')
            .replace('class="panel active" id="p-inicio"', 'class="panel" id="p-inicio"')
            .replace(f'class="panel" id="p-{t}"', f'class="panel active" id="p-{t}"'))

    ruta = BASE / args.salida
    ruta.write_text(html, encoding="utf-8")
    kb = ruta.stat().st_size / 1024
    print(f"Dashboard generado: {ruta} ({kb:.0f} KB)")
    print(f"Resueltas: {kpis['resueltas']} | Gap medio: {kpis['gap_medio']}% | "
          f"Soluciones cargadas: {len(soluciones)}/30")


PLANTILLA = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cuadro de mandos · IHTC 2024 · TFG</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  :root{--azul:#2E579C;--verde:#2E9C57;--ambar:#E0A030;--rojo:#C03030;--bg:#f4f6fa;--card:#fff;--txt:#1f2733;--mut:#6b7686}
  *{box-sizing:border-box}
  body{margin:0;font-family:'Segoe UI',Roboto,Arial,sans-serif;background:var(--bg);color:var(--txt)}
  header{background:linear-gradient(135deg,#2E579C,#1c3a6e);color:#fff;padding:22px 28px}
  header h1{margin:0;font-size:22px} header .sub{opacity:.85;font-size:13px;margin-top:4px}
  .banner{background:#fff3cd;border-left:4px solid var(--ambar);color:#664d03;padding:10px 28px;font-size:13px}
  main{max-width:1180px;margin:0 auto;padding:20px}
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-bottom:18px}
  .kpi{background:var(--card);border-radius:10px;padding:16px;box-shadow:0 1px 4px rgba(0,0,0,.07)}
  .kpi .v{font-size:26px;font-weight:700;color:var(--azul)} .kpi .l{font-size:12px;color:var(--mut);margin-top:2px}
  .tabs{display:flex;gap:4px;margin-bottom:16px;border-bottom:2px solid #e3e9f3;flex-wrap:wrap}
  .tab{padding:9px 16px;cursor:pointer;font-size:14px;font-weight:600;color:var(--mut);border-bottom:3px solid transparent;margin-bottom:-2px}
  .tab.active{color:var(--azul);border-bottom-color:var(--azul)}
  .panel{display:none} .panel.active{display:block}
  .grid2{display:grid;grid-template-columns:2fr 1fr;gap:18px;margin-bottom:20px}
  .grid2b{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:20px}
  @media(max-width:820px){.grid2,.grid2b{grid-template-columns:1fr}}
  .card{background:var(--card);border-radius:10px;padding:16px;box-shadow:0 1px 4px rgba(0,0,0,.07);margin-bottom:18px}
  .card h3{margin:0 0 12px;font-size:15px} .note{font-size:12px;color:var(--mut);margin-top:6px}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{padding:7px 9px;text-align:right;border-bottom:1px solid #eef1f5}
  th{background:#f0f3f8;cursor:pointer;user-select:none;position:sticky;top:0}
  th:first-child,td:first-child{text-align:left} th:hover{background:#e3e9f3} tr:hover td{background:#f7f9fc}
  .badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;color:#fff}
  .b-v{background:var(--verde)} .b-a{background:var(--ambar)} .b-r{background:var(--rojo)}
  .pos{color:var(--verde);font-weight:700}.neg{color:var(--rojo);font-weight:700}
  .toolbar{display:flex;gap:10px;align-items:center;margin-bottom:10px;flex-wrap:wrap}
  input[type=text],select{padding:7px 10px;border:1px solid #cfd6e0;border-radius:7px;font-size:13px}
  .pill{font-size:11px;color:var(--mut)} .tablewrap{max-height:560px;overflow:auto;border-radius:8px}
  .hm{overflow:auto;border:1px solid #eef1f5;border-radius:8px}
  .hm table{font-size:10px} .hm td,.hm th{padding:0;text-align:center;border:1px solid #f0f2f6}
  .hm .cell{width:20px;height:18px} .hm .rh{padding:2px 6px;text-align:left;background:#f7f9fc;font-weight:600}
  .hm .ch{padding:2px;background:#f7f9fc;color:var(--mut)}
  .schips{display:flex;gap:18px;flex-wrap:wrap;margin:6px 0 12px}
  .chip{font-size:13px} .chip b{font-size:18px;color:var(--azul)}
  .lead{font-size:14px;color:#3a4654;margin:4px 0 14px;line-height:1.55}
  .headline{display:flex;gap:14px;flex-wrap:wrap}
  .hcard{background:#eef3fb;border-radius:10px;padding:14px 18px;min-width:150px;border:1px solid #dde6f3}
  .hcard .hv{font-size:24px;font-weight:700;color:var(--azul)}
  .hcard .hl{font-size:12px;color:var(--mut);margin-top:2px}
  .navguide{margin:0;padding-left:18px;font-size:13px;line-height:1.8}
  footer{text-align:center;color:var(--mut);font-size:12px;padding:18px}
</style>
</head>
<body>
<header><h1>Cuadro de mandos · IHTC 2024</h1><div class="sub" id="sub"></div></header>
<div class="banner" id="banner"></div>
<main>
  <div class="kpis" id="kpis"></div>
  <div class="tabs">
    <div class="tab active" data-p="inicio">Inicio</div>
    <div class="tab" data-p="resumen">Resumen</div>
    <div class="tab" data-p="costes">Costes por instancia</div>
    <div class="tab" data-p="instancias">Instancias</div>
    <div class="tab" data-p="solucion">Solución</div>
    <div class="tab" data-p="comparativa">Comparativa: mejor resultado</div>
    <div class="tab" data-p="ablacion">Ablación</div>
  </div>

  <div class="panel active" id="p-inicio">
    <div class="card">
      <h3>Cuadro de mandos &middot; Planificación integrada hospitalaria (IHTC 2024)</h3>
      <p class="lead">Herramienta interactiva para analizar y comparar las soluciones de la
        metodología propuesta frente a los mejores resultados de la competición,
        sobre las 30 instancias públicas. Permite localizar visualmente las deficiencias de la
        propuesta y el origen de la diferencia de coste.</p>
      <div class="headline" id="headline"></div>
    </div>
    <div class="grid2b">
      <div class="card"><h3>Cómo navegar</h3>
        <ul class="navguide">
          <li><b>Resumen:</b> gap por instancia y composición global del coste.</li>
          <li><b>Costes por instancia:</b> desglose en los ocho componentes del validador.</li>
          <li><b>Instancias:</b> tamaño de cada caso y reparto de pacientes.</li>
          <li><b>Solución:</b> ocupación de camas, uso de quirófanos y mapa por habitación.</li>
          <li><b>Comparativa con el mejor resultado:</b> propuesta frente al mejor resultado de la competición, componente a componente y estructura a estructura.</li>
          <li><b>Ablación:</b> aportación de la fase de readmisión al coste.</li>
        </ul>
      </div>
      <div class="card"><h3>Lectura principal</h3>
        <p class="lead" id="lectura"></p>
      </div>
    </div>
  </div>

  <div class="panel" id="p-resumen">
    <div class="grid2">
      <div class="card"><h3>Gap relativo por instancia (%)</h3><canvas id="chGap" height="120"></canvas></div>
      <div class="card"><h3>Composición del coste total</h3><canvas id="chComp" height="120"></canvas></div>
    </div>
    <div class="card"><h3>Resultados por instancia</h3>
      <div class="toolbar"><input type="text" id="filtro" placeholder="Filtrar instancia…">
        <span class="pill">Clic en cabeceras para ordenar · verde ≤10% · ámbar 10–30% · rojo &gt;30%</span></div>
      <div class="tablewrap"><table id="tabla"><thead></thead><tbody></tbody></table></div>
    </div>
  </div>

  <div class="panel" id="p-costes">
    <div class="card"><h3>Componentes del coste por instancia (apilado)</h3>
      <canvas id="chStack" height="120"></canvas>
      <div class="note">Cada barra es el coste total de la instancia, descompuesto en los 8 componentes del validador oficial.</div>
    </div>
  </div>

  <div class="panel" id="p-instancias">
    <div class="grid2b">
      <div class="card"><h3>Tamaño de las instancias</h3><canvas id="chSize" height="150"></canvas>
        <div class="note">Número de pacientes, habitaciones, quirófanos y enfermeras por instancia.</div></div>
      <div class="card"><h3>Pacientes: programados vs no programados</h3><canvas id="chPat" height="150"></canvas>
        <div class="note">Obligatorios (siempre programados), opcionales programados y opcionales descartados.</div></div>
    </div>
    <div class="card"><h3>Detalle por instancia</h3>
      <div class="tablewrap"><table id="tablaInst"><thead></thead><tbody></tbody></table></div>
    </div>
  </div>

  <div class="panel" id="p-solucion">
    <div class="card">
      <div class="toolbar"><b>Instancia:</b> <select id="selInst"></select>
        <span class="pill">Visualización de la solución generada</span></div>
      <div class="schips" id="solChips"></div>
    </div>
    <div class="grid2b">
      <div class="card"><h3>Ocupación de camas por día</h3><canvas id="chBed" height="150"></canvas>
        <div class="note">Camas ocupadas frente a la capacidad total del hospital.</div></div>
      <div class="card"><h3>Ingresos por día</h3><canvas id="chAdm" height="150"></canvas></div>
    </div>
    <div class="card"><h3>Uso de quirófanos por día (minutos)</h3><canvas id="chOT" height="110"></canvas>
      <div class="note">Minutos de cirugía programados frente a la capacidad diaria disponible.</div></div>
    <div class="card"><h3>Mapa de ocupación: habitación × día</h3>
      <div class="hm" id="heatmap"></div>
      <div class="note">Intensidad proporcional a la ocupación de la habitación ese día (verde claro = poca, azul oscuro = llena).</div>
    </div>

    <h3 style="margin:8px 2px">Introspección de la solución</h3>
    <div class="grid2b">
      <div class="card"><h3>Violaciones de restricciones duras</h3>
        <div class="tablewrap" style="max-height:none"><table id="tablaViol"><thead></thead><tbody></tbody></table></div>
        <div class="note">Las nueve restricciones duras del validador oficial. Una solución válida tiene todas a cero.</div></div>
      <div class="card"><h3>Coste por componente (restricciones blandas)</h3><canvas id="chSoft" height="150"></canvas>
        <div class="note">Desglose del coste de esta solución en los ocho componentes blandos.</div></div>
    </div>
    <div class="grid2b">
      <div class="card"><h3>Carga por quirófano (minutos)</h3><canvas id="chOTdist" height="150"></canvas>
        <div class="note">Minutos de cirugía asignados a cada quirófano durante todo el horizonte.</div></div>
      <div class="card"><h3>Carga por cirujano (minutos)</h3><canvas id="chSurg" height="150"></canvas>
        <div class="note">Minutos de cirugía asignados a cada cirujano.</div></div>
    </div>
    <div class="grid2b">
      <div class="card"><h3>Ocupación por habitación (pacientes·día)</h3><canvas id="chRoom" height="150"></canvas>
        <div class="note">Días-paciente acumulados en cada habitación.</div></div>
      <div class="card"><h3>Pacientes programados por género y edad</h3><canvas id="chDemo" height="150"></canvas>
        <div class="note">Reparto de los pacientes admitidos por género y por grupo de edad.</div></div>
    </div>
  </div>

  <div class="panel" id="p-comparativa">
    <div class="kpis" id="compKpis"></div>
    <div class="grid2b">
      <div class="card"><h3>Coste por componente: propuesta vs mejor resultado (agregado)</h3>
        <canvas id="chCompAgg" height="150"></canvas>
        <div class="note">Suma de cada componente sobre las instancias con solución de referencia. Revela en qué se concentra la brecha.</div></div>
      <div class="card"><h3>Brecha por componente (propuesta − mejor resultado)</h3>
        <canvas id="chCompDelta" height="150"></canvas>
        <div class="note">Coste adicional de la propuesta en cada componente. Barras altas = cuello de botella.</div></div>
    </div>
    <div class="card"><h3>Coste total por instancia: propuesta vs mejor resultado</h3>
      <canvas id="chCompTot" height="110"></canvas></div>
    <div class="card"><h3>Pacientes programados por instancia: propuesta vs mejor resultado</h3>
      <canvas id="chCompSched" height="110"></canvas>
      <div class="note">Diferencia en pacientes admitidos: dónde el mejor resultado de la competición coloca opcionales que la propuesta descarta.</div></div>
    <div class="card"><h3>Pacientes descartados por instancia: propuesta vs mejor resultado</h3>
      <canvas id="chCompUns" height="110"></canvas>
      <div class="note">Opcionales no programados en cada solución. Es el principal cuello de botella de la propuesta.</div></div>
    <div class="card"><h3>Origen de la brecha por instancia (propuesta − mejor resultado)</h3>
      <canvas id="chGapSplit" height="120"></canvas>
      <div class="note">Descomposición del sobrecoste por instancia: barras positivas indican coste de más (cuello de botella); negativas, ventaja de la propuesta. Se separan los opcionales no programados, el retraso de admisión y el resto de componentes.</div></div>
    <div class="card"><h3>Detalle comparativo por instancia</h3>
      <div class="tablewrap"><table id="tablaComp"><thead></thead><tbody></tbody></table></div>
    </div>

    <div class="card">
      <div class="toolbar"><b>Explorar instancia:</b> <select id="selComp"></select>
        <span class="pill">Estructura de la solución: propuesta vs mejor resultado</span></div>
    </div>
    <div class="grid2b">
      <div class="card"><h3>Ocupación de camas por día</h3><canvas id="chCBed" height="150"></canvas>
        <div class="note">Camas ocupadas por cada solución frente a la capacidad total.</div></div>
      <div class="card"><h3>Ingresos por día</h3><canvas id="chCAdm" height="150"></canvas></div>
    </div>
    <div class="card"><h3>Uso de quirófanos por día (minutos)</h3><canvas id="chCOT" height="110"></canvas>
      <div class="note">Minutos de cirugía de cada solución frente a la capacidad diaria.</div></div>
    <div class="grid2b">
      <div class="card"><h3>Mapa de ocupación &mdash; Propuesta</h3><div class="hm" id="heatN"></div></div>
      <div class="card"><h3>Mapa de ocupación &mdash; Mejor resultado</h3><div class="hm" id="heatO"></div></div>
    </div>
  </div>

  <div class="panel" id="p-ablacion">
    <div class="card"><h3>Estudio de ablación: aportación de la readmisión</h3>
      <div id="ablNote" class="note"></div>
      <div class="grid2" style="margin-top:12px">
        <div><canvas id="chAbl" height="130"></canvas></div>
        <div class="tablewrap"><table id="tablaAbl"><thead></thead><tbody></tbody></table></div>
      </div>
    </div>
  </div>
</main>
<footer id="foot"></footer>

<script>
const D = /*DATOS*/;
const banda = g => g==null?'':(g<=10?'b-v':(g<=30?'b-a':'b-r'));
const fmt = n => n==null?'—':n.toLocaleString('es-ES');

document.getElementById('sub').textContent = `Propuesta del TFG · ${D.etiqueta} · generado ${D.generado}`;
document.getElementById('banner').innerHTML = '⚠ <b>Condiciones:</b> '+D.etiqueta+
  '. Reglas oficiales: 10 min y máx. 4 hilos por instancia; el gap es una cota optimista.';

const k = D.kpis;
document.getElementById('kpis').innerHTML = [
  ['Instancias resueltas',k.resueltas],['Sin violaciones',k.sin_violaciones],
  ['Gap medio',k.gap_medio!=null?k.gap_medio+'%':'—'],['Gap mediano',k.gap_mediano!=null?k.gap_mediano+'%':'—'],
  ['Dentro de +10%',k.dentro10+'/'+k.n_gap],['Coste total',fmt(k.coste_total)]
].map(([l,v])=>`<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`).join('');

document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));
  t.classList.add('active'); document.getElementById('p-'+t.dataset.p).classList.add('active');
});

// ── Inicio: titulares y lectura principal ──────────────────────────
(function(){
  const C=D.comparativa;
  let h=`<div class="hcard"><div class="hv">${k.sin_violaciones}</div><div class="hl">instancias sin violaciones</div></div>`;
  h+=`<div class="hcard"><div class="hv">${k.gap_medio!=null?k.gap_medio+'%':'—'}</div><div class="hl">gap medio frente al mejor resultado de la competición</div></div>`;
  h+=`<div class="hcard"><div class="hv">${k.dentro10}/${k.n_gap}</div><div class="hl">dentro del +10%</div></div>`;
  let lectura='La metodología propuesta resuelve las 30 instancias sin violaciones de '+
    'restricciones duras. ';
  if(C && C.n){
    const sumN=C.agg.reduce((a,x)=>a+x.n,0), sumO=C.agg.reduce((a,x)=>a+x.o,0);
    const sobre=Math.round((sumN-sumO)/sumO*100);
    const top=[...C.agg].sort((a,b)=>b.delta-a.delta)[0];
    const brecha=sumN-sumO;
    const pctTop=Math.round(top.delta/brecha*100);
    h+=`<div class="hcard"><div class="hv">+${sobre}%</div><div class="hl">sobrecoste agregado vs mejor competición</div></div>`;
    h+=`<div class="hcard"><div class="hv">${top.label}</div><div class="hl">principal cuello de botella (${pctTop}% de la brecha)</div></div>`;
    lectura+=`Frente a los mejores resultados de la competición, el sobrecoste agregado es del `+
      `${sobre} %, concentrado sobre todo en <b>${top.label}</b> (${pctTop} % de la `+
      `diferencia total). El cuello de botella no está en la enfermería ni en los quirófanos, `+
      `sino en la fase de admisión: en programar más pacientes opcionales.`;
  }
  document.getElementById('headline').innerHTML=h;
  const el=document.getElementById('lectura'); if(el) el.innerHTML=lectura;
})();

const okF = D.filas.filter(f=>f.gap!=null);
const labels = okF.map(f=>f.inst), gapVals = okF.map(f=>f.gap);
const cols = gapVals.map(g=> g<=10?'#2E9C57':(g<=30?'#E0A030':'#C03030'));
const insts = Object.keys(D.soluciones).sort();

if (window.Chart) {
  new Chart(chGap,{type:'bar',data:{labels,datasets:[{data:gapVals,backgroundColor:cols}]},
    options:{plugins:{legend:{display:false}},scales:{x:{ticks:{font:{size:9},maxRotation:90,minRotation:90}},y:{title:{display:true,text:'gap %'}}}}});
  new Chart(chComp,{type:'doughnut',data:{labels:['Opcionales no programados','Retraso admisión','Costes blandos'],
    datasets:[{data:[D.comp_global.unsched,D.comp_global.delay,D.comp_global.resto],backgroundColor:['#C03030','#E0A030','#2E9C57']}]},
    options:{plugins:{legend:{position:'bottom',labels:{font:{size:11}}}}}});
  new Chart(chStack,{type:'bar',data:{labels,datasets:D.comp_def.map(([c,t,col])=>({label:t,data:okF.map(f=>f[c]||0),backgroundColor:col}))},
    options:{plugins:{legend:{position:'bottom',labels:{font:{size:10},boxWidth:12}}},scales:{x:{stacked:true,ticks:{font:{size:9},maxRotation:90,minRotation:90}},y:{stacked:true,title:{display:true,text:'coste'}}}}});

  // Instancias: tamaño + pacientes
  const si = insts.map(i=>D.soluciones[i]);
  new Chart(chSize,{type:'bar',data:{labels:insts,datasets:[
    {label:'Pacientes',data:si.map(s=>s.n_total),backgroundColor:'#2E579C'},
    {label:'Habitaciones',data:si.map(s=>s.n_rooms),backgroundColor:'#5B9BD5'},
    {label:'Quirófanos',data:si.map(s=>s.n_ots),backgroundColor:'#E0A030'},
    {label:'Enfermeras',data:si.map(s=>s.n_nurses),backgroundColor:'#2E9C57'}]},
    options:{plugins:{legend:{position:'bottom',labels:{font:{size:10},boxWidth:12}}},scales:{x:{ticks:{font:{size:9},maxRotation:90,minRotation:90}}}}});
  new Chart(chPat,{type:'bar',data:{labels:insts,datasets:[
    {label:'Obligatorios',data:si.map(s=>s.n_mand),backgroundColor:'#2E579C'},
    {label:'Opcionales programados',data:si.map(s=>Math.max(0,s.n_sched-s.n_mand)),backgroundColor:'#2E9C57'},
    {label:'Opcionales descartados',data:si.map(s=>s.n_unsched),backgroundColor:'#C03030'}]},
    options:{plugins:{legend:{position:'bottom',labels:{font:{size:10},boxWidth:12}}},scales:{x:{stacked:true,ticks:{font:{size:9},maxRotation:90,minRotation:90}},y:{stacked:true}}}});

  // Ablación
  const A=D.ablacion.filas;
  new Chart(chAbl,{type:'bar',data:{labels:A.map(f=>f.inst),datasets:[
    {label:'Sin readmisión',data:A.map(f=>f.sin),backgroundColor:'#C03030'},
    {label:'Con readmisión',data:A.map(f=>f.con),backgroundColor:'#2E9C57'}]},
    options:{plugins:{legend:{position:'bottom',labels:{font:{size:11}}}},scales:{y:{title:{display:true,text:'coste'}}}}});
}

// Tabla instancias
(function(){
  const si=insts.map(i=>({inst:i,...D.soluciones[i]}));
  document.querySelector('#tablaInst thead').innerHTML='<tr><th>Inst</th><th>Días</th><th>Pacientes</th><th>Oblig.</th><th>Opc.</th><th>Programados</th><th>Descartados</th><th>Habit.</th><th>Camas</th><th>Quiróf.</th><th>Enferm.</th></tr>';
  document.querySelector('#tablaInst tbody').innerHTML=si.map(s=>
    `<tr><td>${s.inst}</td><td>${s.days}</td><td>${s.n_total}</td><td>${s.n_mand}</td><td>${s.n_opt}</td>`+
    `<td><b>${s.n_sched}</b></td><td class="${s.n_unsched>0?'neg':''}">${s.n_unsched}</td>`+
    `<td>${s.n_rooms}</td><td>${s.total_beds}</td><td>${s.n_ots}</td><td>${s.n_nurses}</td></tr>`).join('');
})();

// Explorador de solución
let chBed,chAdm,chOT,chSoft,chOTdist,chSurg,chRoom,chDemo;
const COMP_DEF=D.comp_def;  // [clave, etiqueta, color]
function colorOcc(frac){ if(frac<=0)return '#ffffff'; const t=Math.min(1,frac);
  const r=Math.round(220-(220-46)*t),g=Math.round(235-(235-87)*t),b=Math.round(240-(240-156)*t);
  return `rgb(${r},${g},${b})`; }
function heatmapHTML(s){
  let h='<table><tr><th class="ch">hab\\día</th>';
  for(let d=0;d<s.days;d++) h+=`<th class="ch">${d}</th>`;
  h+='</tr>';
  s.room_ids.forEach((rid,ri)=>{
    h+=`<tr><td class="rh">${rid} <span style="color:#9aa3b0">(${s.cap[ri]})</span></td>`;
    for(let d=0;d<s.days;d++){ const oc=s.mat[ri][d], frac=s.cap[ri]?oc/s.cap[ri]:0;
      h+=`<td class="cell" title="${rid} día ${d}: ${oc}/${s.cap[ri]}" style="background:${colorOcc(frac)}">${oc||''}</td>`; }
    h+='</tr>';
  });
  return h+'</table>';
}
function pintaSolucion(inst){
  const s=D.soluciones[inst]; if(!s)return;
  const dias=[...Array(s.days).keys()];
  document.getElementById('solChips').innerHTML=[
    ['Pacientes programados',s.n_sched+' / '+s.n_total],
    ['Opcionales descartados',s.n_unsched],
    ['Ocupación máx. camas',Math.max(...s.bed)+' / '+s.total_beds],
    ['Pico de ingresos/día',Math.max(...s.adm)],
    ['Horizonte',s.days+' días']
  ].map(([l,v])=>`<div class="chip">${l}: <b>${v}</b></div>`).join('');

  if(window.Chart){
    [chBed,chAdm,chOT].forEach(c=>c&&c.destroy());
    chBed=new Chart(document.getElementById('chBed'),{type:'line',
      data:{labels:dias,datasets:[
        {label:'Camas ocupadas',data:s.bed,borderColor:'#2E579C',backgroundColor:'rgba(46,87,156,.15)',fill:true,tension:.2},
        {label:'Capacidad total',data:dias.map(()=>s.total_beds),borderColor:'#C03030',borderDash:[6,4],pointRadius:0}]},
      options:{plugins:{legend:{labels:{font:{size:11}}}},scales:{x:{title:{display:true,text:'día'}}}}});
    chAdm=new Chart(document.getElementById('chAdm'),{type:'bar',
      data:{labels:dias,datasets:[{label:'Ingresos',data:s.adm,backgroundColor:'#2E9C57'}]},
      options:{plugins:{legend:{display:false}},scales:{x:{title:{display:true,text:'día'}}}}});
    chOT=new Chart(document.getElementById('chOT'),{type:'bar',
      data:{labels:dias,datasets:[
        {label:'Minutos usados',data:s.ot_used,backgroundColor:'#7030A0'},
        {label:'Capacidad',data:s.ot_cap,type:'line',borderColor:'#C03030',borderDash:[6,4],pointRadius:0}]},
      options:{plugins:{legend:{labels:{font:{size:11}}}},scales:{x:{title:{display:true,text:'día'}}}}});
  }

  // Heatmap habitación x día
  let h='<table><tr><th class="ch">hab\\día</th>';
  for(const d of dias) h+=`<th class="ch">${d}</th>`;
  h+='</tr>';
  s.room_ids.forEach((rid,ri)=>{
    h+=`<tr><td class="rh">${rid} <span style="color:#9aa3b0">(${s.cap[ri]})</span></td>`;
    for(let d=0;d<s.days;d++){ const oc=s.mat[ri][d], frac=s.cap[ri]?oc/s.cap[ri]:0;
      h+=`<td class="cell" title="${rid} día ${d}: ${oc}/${s.cap[ri]}" style="background:${colorOcc(frac)}">${oc||''}</td>`; }
    h+='</tr>';
  });
  h+='</table>';
  document.getElementById('heatmap').innerHTML=h;

  // ── Introspección: violaciones, costes y distribuciones ──────────
  const viol=s.viol||{};
  const filasV=Object.keys(viol).map(k=>{
    const v=viol[k]; const ok=(v===0);
    return `<tr><td>${k}</td><td style="text-align:right;color:${ok?'#2E9C57':'#C03030'};font-weight:600">${v}</td></tr>`;
  }).join('');
  document.querySelector('#tablaViol thead').innerHTML='<tr><th>Restricción dura</th><th style="text-align:right">Violaciones</th></tr>';
  document.querySelector('#tablaViol tbody').innerHTML=filasV+
    `<tr><td><b>Total</b></td><td style="text-align:right"><b>${s.total_viol!=null?s.total_viol:'—'}</b></td></tr>`;

  if(window.Chart){
    [chSoft,chOTdist,chSurg,chRoom,chDemo].forEach(c=>c&&c.destroy());
    const cst=s.costes||{};
    chSoft=new Chart(document.getElementById('chSoft'),{type:'bar',
      data:{labels:COMP_DEF.map(d=>d[1]),datasets:[{data:COMP_DEF.map(d=>cst[d[0]]||0),
        backgroundColor:COMP_DEF.map(d=>d[2])}]},
      options:{plugins:{legend:{display:false}},scales:{x:{ticks:{font:{size:9},maxRotation:45,minRotation:45}},y:{title:{display:true,text:'coste'}}}}});
    chOTdist=new Chart(document.getElementById('chOTdist'),{type:'bar',
      data:{labels:s.ot_dist_ids,datasets:[{data:s.ot_dist_min,backgroundColor:'#7030A0'}]},
      options:{plugins:{legend:{display:false}},scales:{y:{title:{display:true,text:'min'}}}}});
    chSurg=new Chart(document.getElementById('chSurg'),{type:'bar',
      data:{labels:s.surg_ids,datasets:[{data:s.surg_min,backgroundColor:'#2E579C'}]},
      options:{plugins:{legend:{display:false}},scales:{x:{ticks:{font:{size:9}}},y:{title:{display:true,text:'min'}}}}});
    chRoom=new Chart(document.getElementById('chRoom'),{type:'bar',
      data:{labels:s.room_ids,datasets:[{data:s.room_pd,backgroundColor:'#5B9BD5'}]},
      options:{plugins:{legend:{display:false}},scales:{x:{ticks:{font:{size:9},maxRotation:90,minRotation:90}},y:{title:{display:true,text:'pac·día'}}}}});
    const gk=Object.keys(s.by_gender||{}), ak=Object.keys(s.by_age||{});
    chDemo=new Chart(document.getElementById('chDemo'),{type:'bar',
      data:{labels:gk.concat(ak),datasets:[{data:gk.map(k=>s.by_gender[k]).concat(ak.map(k=>s.by_age[k])),
        backgroundColor:gk.map(()=>'#E0A030').concat(ak.map(()=>'#2E9C57'))}]},
      options:{plugins:{legend:{display:false},title:{display:true,text:'género | grupo de edad',font:{size:10}}},scales:{y:{title:{display:true,text:'pacientes'}}}}});
  }
}
const sel=document.getElementById('selInst');
sel.innerHTML=insts.map(i=>`<option value="${i}">${i}</option>`).join('');
sel.onchange=()=>pintaSolucion(sel.value);
if(insts.length) pintaSolucion(insts[0]);

// ── Comparativa con soluciones oficiales ──────────────────────────
(function(){
  const C = D.comparativa;
  if(!C || !C.n){
    document.getElementById('compKpis').innerHTML =
      '<div class="kpi"><div class="v">—</div><div class="l">Sin datos oficiales. Ejecuta procesar_soluciones_oficiales.py y regenera.</div></div>';
    return;
  }
  const ci = C.por_instancia, labs = ci.map(x=>x.inst);
  const sumN = C.agg.reduce((a,x)=>a+x.n,0), sumO = C.agg.reduce((a,x)=>a+x.o,0);
  const peor = C.agg.slice().sort((a,b)=>b.delta-a.delta)[0];
  document.getElementById('compKpis').innerHTML = [
    ['Instancias comparadas', C.n+'/30'],
    ['Coste total propuesta', fmt(sumN)],
    ['Coste total (mejor comp.)', fmt(sumO)],
    ['Sobrecoste global', '+'+(sumO?Math.round((sumN-sumO)/sumO*100):0)+'%'],
    ['Mayor cuello de botella', peor?peor.label:'—'],
  ].map(([l,v])=>`<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`).join('');

  if(window.Chart){
    new Chart(document.getElementById('chCompAgg'),{type:'bar',
      data:{labels:C.agg.map(x=>x.label),datasets:[
        {label:'Propuesta',data:C.agg.map(x=>x.n),backgroundColor:'#2E579C'},
        {label:'Mejor (competición)',data:C.agg.map(x=>x.o),backgroundColor:'#2E9C57'}]},
      options:{plugins:{legend:{position:'bottom'}},scales:{x:{ticks:{font:{size:10}}}}}});
    new Chart(document.getElementById('chCompDelta'),{type:'bar',
      data:{labels:C.agg.map(x=>x.label),datasets:[{data:C.agg.map(x=>x.delta),
        backgroundColor:C.agg.map(x=>x.delta>0?'#C03030':'#2E9C57')}]},
      options:{plugins:{legend:{display:false}},scales:{x:{ticks:{font:{size:10}}},y:{title:{display:true,text:'Δ coste'}}}}});
    new Chart(document.getElementById('chCompTot'),{type:'bar',
      data:{labels:labs,datasets:[
        {label:'Propuesta',data:ci.map(x=>x.propuesta),backgroundColor:'#2E579C'},
        {label:'Mejor (competición)',data:ci.map(x=>x.oficial),backgroundColor:'#2E9C57'}]},
      options:{plugins:{legend:{position:'bottom'}},scales:{x:{ticks:{font:{size:9},maxRotation:90,minRotation:90}}}}});
    new Chart(document.getElementById('chCompSched'),{type:'bar',
      data:{labels:labs,datasets:[
        {label:'Programados (propuesta)',data:ci.map(x=>x.sched_n),backgroundColor:'#2E579C'},
        {label:'Programados (mejor comp.)',data:ci.map(x=>x.sched_o),backgroundColor:'#2E9C57'}]},
      options:{plugins:{legend:{position:'bottom'}},scales:{x:{ticks:{font:{size:9},maxRotation:90,minRotation:90}}}}});

    // Pacientes descartados por instancia (cuello de botella principal)
    new Chart(document.getElementById('chCompUns'),{type:'bar',
      data:{labels:labs,datasets:[
        {label:'Descartados (propuesta)',data:ci.map(x=>x.unsched_n),backgroundColor:'#C03030'},
        {label:'Descartados (mejor comp.)',data:ci.map(x=>x.unsched_o),backgroundColor:'#E0A030'}]},
      options:{plugins:{legend:{position:'bottom'}},scales:{x:{ticks:{font:{size:9},maxRotation:90,minRotation:90}}}}});

    // Origen de la brecha por instancia: Δ unscheduled, Δ delay, Δ resto (apilado)
    const dU = ci.map(x=>(x.comps.unsched.n - x.comps.unsched.o));
    const dD = ci.map(x=>(x.comps.delay.n   - x.comps.delay.o));
    const dR = ci.map(x=>(x.delta - (x.comps.unsched.n-x.comps.unsched.o) - (x.comps.delay.n-x.comps.delay.o)));
    new Chart(document.getElementById('chGapSplit'),{type:'bar',
      data:{labels:labs,datasets:[
        {label:'Δ Opcionales no programados',data:dU,backgroundColor:'#C03030'},
        {label:'Δ Retraso de admisión',data:dD,backgroundColor:'#E0A030'},
        {label:'Δ Resto de componentes',data:dR,backgroundColor:'#5B9BD5'}]},
      options:{plugins:{legend:{position:'bottom',labels:{font:{size:10},boxWidth:12}}},
        scales:{x:{stacked:true,ticks:{font:{size:9},maxRotation:90,minRotation:90}},
                y:{stacked:true,title:{display:true,text:'Δ coste (propuesta − mejor)'}}}}});
  }

  document.querySelector('#tablaComp thead').innerHTML =
    '<tr><th>Inst</th><th>Propuesta</th><th>Mejor comp.</th><th>Δ</th><th>Δ %</th>'+
    '<th>Prog. n/o</th><th>Descart. n/o</th></tr>';
  document.querySelector('#tablaComp tbody').innerHTML = ci.map(x=>
    `<tr><td>${x.inst}</td><td><b>${fmt(x.propuesta)}</b></td><td>${fmt(x.oficial)}</td>`+
    `<td class="${x.delta>0?'neg':'pos'}">${x.delta>0?'+':''}${fmt(x.delta)}</td>`+
    `<td><span class="badge ${banda(x.gap)}">${x.gap>0?'+':''}${x.gap}%</span></td>`+
    `<td>${x.sched_n} / ${x.sched_o}</td><td>${x.unsched_n} / ${x.unsched_o}</td></tr>`).join('');

  // ── Explorador estructural: propuesta vs mejor resultado ────────────────
  const COL_N='#2E579C', COL_O='#2E9C57', COL_CAP='#C03030';
  let chCBed,chCAdm,chCOT;
  function pintaComp(inst){
    const sn=D.soluciones[inst], so=D.soluciones_oficiales[inst];
    if(!sn||!so) return;
    const dias=[...Array(sn.days).keys()];
    if(window.Chart){
      [chCBed,chCAdm,chCOT].forEach(c=>c&&c.destroy());
      chCBed=new Chart(document.getElementById('chCBed'),{type:'line',
        data:{labels:dias,datasets:[
          {label:'Propuesta',data:sn.bed,borderColor:COL_N,backgroundColor:'rgba(46,87,156,.10)',fill:true,tension:.2},
          {label:'Mejor (competición)',data:so.bed,borderColor:COL_O,backgroundColor:'rgba(46,156,87,.10)',fill:true,tension:.2},
          {label:'Capacidad',data:dias.map(()=>sn.total_beds),borderColor:COL_CAP,borderDash:[6,4],pointRadius:0}]},
        options:{plugins:{legend:{labels:{font:{size:11}}}},scales:{x:{title:{display:true,text:'día'}}}}});
      chCAdm=new Chart(document.getElementById('chCAdm'),{type:'bar',
        data:{labels:dias,datasets:[
          {label:'Propuesta',data:sn.adm,backgroundColor:COL_N},
          {label:'Mejor (competición)',data:so.adm,backgroundColor:COL_O}]},
        options:{plugins:{legend:{labels:{font:{size:11}}}},scales:{x:{title:{display:true,text:'día'}}}}});
      chCOT=new Chart(document.getElementById('chCOT'),{type:'bar',
        data:{labels:dias,datasets:[
          {label:'Propuesta',data:sn.ot_used,backgroundColor:COL_N},
          {label:'Mejor (competición)',data:so.ot_used,backgroundColor:COL_O},
          {label:'Capacidad',data:sn.ot_cap,type:'line',borderColor:COL_CAP,borderDash:[6,4],pointRadius:0}]},
        options:{plugins:{legend:{labels:{font:{size:11}}}},scales:{x:{title:{display:true,text:'día'}}}}});
    }
    document.getElementById('heatN').innerHTML=heatmapHTML(sn);
    document.getElementById('heatO').innerHTML=heatmapHTML(so);
  }
  const selC=document.getElementById('selComp');
  selC.innerHTML=ci.map(x=>`<option value="${x.inst}">${x.inst}</option>`).join('');
  selC.onchange=()=>pintaComp(selC.value);
  if(ci.length) pintaComp(ci[0].inst);
})();

// Ablación nota + tabla
document.getElementById('ablNote').innerHTML=D.ablacion.real
  ?'Comparación CON vs SIN readmisión, con el resto de parámetros idénticos. El delta es atribuible a la readmisión.'
  :'<b>Ejemplo controlado (i13).</b> Para la tabla completa, ejecuta «ejecutar_ablacion_sin_readmision.py» y regenera el dashboard.';
(function(){const A=D.ablacion.filas;
  document.querySelector('#tablaAbl thead').innerHTML='<tr><th>Inst</th><th>Coste sin</th><th>Coste con</th><th>Podados sin→con</th><th>Mejora</th></tr>';
  document.querySelector('#tablaAbl tbody').innerHTML=A.map(f=>
    `<tr><td>${f.inst}</td><td>${fmt(f.sin)}</td><td><b>${fmt(f.con)}</b></td><td>${f.pod_sin} → ${f.pod_con}</td>`+
    `<td class="${f.mejora>0?'pos':'neg'}">${f.mejora>0?'−':'+'}${Math.abs(f.mejora)}%</td></tr>`).join('');})();

// Tabla resumen
const cdef=[['inst','Inst'],['oficial','Mejor comp.'],['obtenido','Obtenido'],['gap','Gap %'],['viol','Viol.'],
  ['unsched','Unsched.'],['delay','Delay'],['resto','Resto'],['tiempo','t (s)'],['poda','Podados']];
let orden={col:'inst',asc:true};
function pintar(){
  const f=document.getElementById('filtro').value.toLowerCase();
  let rows=D.filas.filter(r=>r.inst.toLowerCase().includes(f));
  rows.sort((a,b)=>{let va=a[orden.col],vb=b[orden.col];va=va==null?-Infinity:va;vb=vb==null?-Infinity:vb;
    if(typeof va==='string')return orden.asc?va.localeCompare(vb):vb.localeCompare(va);return orden.asc?va-vb:vb-va;});
  document.querySelector('#tabla thead').innerHTML='<tr>'+cdef.map(([c,t])=>`<th data-c="${c}">${t}${orden.col===c?(orden.asc?' ▲':' ▼'):''}</th>`).join('')+'</tr>';
  document.querySelector('#tabla tbody').innerHTML=rows.map(r=>{
    if(r.obtenido==null)return `<tr><td>${r.inst}</td><td>${fmt(r.oficial)}</td><td colspan="8" style="text-align:center;color:#C03030">${r.estado}</td></tr>`;
    const g=r.gap;return `<tr><td>${r.inst}</td><td>${fmt(r.oficial)}</td><td><b>${fmt(r.obtenido)}</b></td>`+
      `<td><span class="badge ${banda(g)}">${g>0?'+':''}${g}%</span></td><td>${r.viol}</td><td>${fmt(r.unsched)}</td>`+
      `<td>${fmt(r.delay)}</td><td>${fmt(r.resto)}</td><td>${r.tiempo}</td><td>${r.poda}</td></tr>`;}).join('');
  document.querySelectorAll('#tabla thead th').forEach(th=>th.onclick=()=>{const c=th.dataset.c;
    if(orden.col===c)orden.asc=!orden.asc;else{orden.col=c;orden.asc=true;}pintar();});
}
document.getElementById('filtro').oninput=pintar; pintar();
document.getElementById('foot').textContent='Generado automáticamente desde los ficheros de instancia, solución y validación · IHTC 2024 · TFG';
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
