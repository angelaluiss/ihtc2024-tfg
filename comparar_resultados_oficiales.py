from pathlib import Path
import pandas as pd

CSV_PIPELINE = Path("resultados_pipeline_completo") / "resumen_pipeline.csv"
CSV_SALIDA = Path("resultados_pipeline_completo") / "comparacion_oficial.csv"

# Mejores costes publicados para el conjunto público i01--i30.
# Fuente: página oficial de IHTC 2024, sección Results.
COSTES_OFICIALES = {
    "i01": 3842,
    "i02": 1264,
    "i03": 10490,
    "i04": 1884,
    "i05": 12760,
    "i06": 10671,
    "i07": 5026,
    "i08": 6291,
    "i09": 6682,
    "i10": 20820,
    "i11": 25938,
    "i12": 12430,
    "i13": 17328,
    "i14": 9746,
    "i15": 12486,
    "i16": 10139,
    "i17": 40535,
    "i18": 37660,
    "i19": 44587,
    "i20": 29098,
    "i21": 24703,
    "i22": 47861,
    "i23": 37550,
    "i24": 33221,
    "i25": 11517,
    "i26": 64613,
    "i27": 51828,
    "i28": 75172,
    "i29": 12475,
    "i30": 37943,
}

df = pd.read_csv(CSV_PIPELINE)

df["instancia"] = (
    df["instance"]
    .astype(str)
    .str.replace(".json", "", regex=False)
)

df["coste_oficial"] = df["instancia"].map(COSTES_OFICIALES)
df["coste_obtenido"] = pd.to_numeric(df["final_total_cost"], errors="coerce")
df["violaciones"] = pd.to_numeric(df["final_total_violations"], errors="coerce")
df["factible"] = df["violaciones"].eq(0)

df["gap_absoluto"] = df["coste_obtenido"] - df["coste_oficial"]

df["gap_relativo_%"] = (
    (df["coste_obtenido"] - df["coste_oficial"])
    / df["coste_oficial"]
    * 100
)

# Si no es factible o no hay coste oficial, no mostramos gap.
df.loc[~df["factible"], ["gap_absoluto", "gap_relativo_%"]] = None
df.loc[df["coste_oficial"].isna(), ["gap_absoluto", "gap_relativo_%"]] = None

columnas = [
    "instancia",
    "factible",
    "violaciones",
    "coste_oficial",
    "coste_obtenido",
    "gap_absoluto",
    "gap_relativo_%",
    "best_seed_fase3",
    "final_RoomAgeMix",
    "final_RoomSkillLevel",
    "final_ContinuityOfCare",
    "final_ExcessiveNurseWorkload",
    "final_OpenOperatingTheater",
    "final_SurgeonTransfer",
    "final_PatientDelay",
    "final_ElectiveUnscheduledPatients",
    "final_solution",
]

comparacion = df[columnas].sort_values("instancia")
comparacion.to_csv(CSV_SALIDA, index=False, encoding="utf-8-sig")

print("\nCOMPARACIÓN CON RESULTADOS OFICIALES")
print("=" * 80)
print(
    comparacion[
        [
            "instancia",
            "factible",
            "violaciones",
            "coste_oficial",
            "coste_obtenido",
            "gap_relativo_%",
            "best_seed_fase3",
        ]
    ].to_string(index=False)
)

validas = comparacion[
    comparacion["factible"]
    & comparacion["coste_oficial"].notna()
    & comparacion["coste_obtenido"].notna()
]

print("\nRESUMEN")
print("=" * 80)
print(f"Instancias comparables factibles: {len(validas)}")

if len(validas) > 0:
    print(f"Gap medio: {validas['gap_relativo_%'].mean():.2f}%")
    print(f"Gap mediano: {validas['gap_relativo_%'].median():.2f}%")

    mejor = validas.loc[validas["gap_relativo_%"].idxmin()]
    peor = validas.loc[validas["gap_relativo_%"].idxmax()]

    print(
        f"Mejor gap: {mejor['instancia']} "
        f"({mejor['gap_relativo_%']:.2f}%)"
    )
    print(
        f"Peor gap: {peor['instancia']} "
        f"({peor['gap_relativo_%']:.2f}%)"
    )

print(f"\nArchivo guardado en: {CSV_SALIDA}")