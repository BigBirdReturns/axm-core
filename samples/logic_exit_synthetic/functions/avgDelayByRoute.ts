// SYNTHETIC — invented Foundry Function source (no real tenant).
import { Objects, Query } from "@foundry/functions-api";

@Query()
export function avgDelayByRoute(route: string): Double {
  const flights = Objects.search().flightsClean().filter(f => f.route === route);
  const delays = flights.all().map(f => f.delayMinutes);
  return delays.length ? delays.reduce((a, b) => a + b, 0) / delays.length : 0;
}
