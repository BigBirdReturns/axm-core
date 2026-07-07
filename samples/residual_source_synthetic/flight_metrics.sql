-- SYNTHETIC — invented Foundry SQL transform (no real tenant).
SELECT route, COUNT(*) AS flightCount, AVG(delayMinutes) AS avgDelayMinutes
FROM `/synth/clean/flights_clean`
GROUP BY route
