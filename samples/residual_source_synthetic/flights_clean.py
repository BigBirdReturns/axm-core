# SYNTHETIC — invented Foundry transform source (no real tenant).
from transforms.api import transform_df, Input, Output

@transform_df(
    Output("/synth/clean/flights_clean"),
    raw=Input("/synth/ingest/raw_flights"),
    ref=Input("/synth/ref/airport_ref"),
)
def compute(raw, ref):
    return raw.join(ref, raw.origin == ref.airportCode, "left")
