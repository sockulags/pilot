---
name: web-lookup
triggers: vad blir det för väder, kolla vädret, slå upp på nätet, sök på webben, aktuella nyheter, hitta information online, dagens kurs; what is the weather, look this up online, current news, search the web, real-time facts
---
The user asks for current or factual information you don't already know (weather,
news, prices, recent facts). Use `web_search(query)` to get results, then
`fetch_url(url)` to read the most relevant one in full before answering. For
weather specifically, fetching `https://wttr.in/<city>?format=3` returns a one
-line current report. Ground the answer in what the tools return — do not say
you "lack a weather API"; you have web access.
