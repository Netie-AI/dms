# STREAM-STAGES-01

Keywords: STREAM-STAGES-01, DMS_ASK_STREAM, SSE, ask stages, cancel, rung

Main idea: `DMS_ASK_STREAM` defaults off. Off, `GET /v1/studio/ask/{id}/events` is not mounted and the ask response matches the flag-on response. On, the ladder's existing stage names are pushed as `{stage, t_ms, status}` with no SQL, row values, or relation names. Cancel aborts the in-flight ask, which is the existing disconnect path. Armed grade52 and pack H were not run. Not PASS.
