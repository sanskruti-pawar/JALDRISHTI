from flask import Flask, jsonify, request, render_template_string
import requests
from datetime import datetime
import math

app = Flask(__name__)

# ============================================================
# JALDRISHTI 
# URBAN FLOOD NOWCASTING + FLOOD-AWARE ROUTING
# SINGLE FILE FLASK APPLICATION
# ============================================================

CITIES = {
    "Nashik": {"lat": 19.9975, "lon": 73.7898},
    "Mumbai": {"lat": 19.0760, "lon": 72.8777},
    "Pune": {"lat": 18.5204, "lon": 73.8567},
    "Delhi": {"lat": 28.6139, "lon": 77.2090},
    "Nagpur": {"lat": 21.1458, "lon": 79.0882},
    "Chennai": {"lat": 13.0827, "lon": 80.2707},
    "Bengaluru": {"lat": 12.9716, "lon": 77.5946},
    "Hyderabad": {"lat": 17.3850, "lon": 78.4867},
}

OSRM_URL = "https://router.project-osrm.org"

WEATHER_CODES = {
    0: "Clear Sky", 1: "Mainly Clear", 2: "Partly Cloudy",
    3: "Overcast", 45: "Fog", 48: "Fog",
    51: "Light Drizzle", 53: "Moderate Drizzle", 55: "Heavy Drizzle",
    61: "Light Rain", 63: "Moderate Rain", 65: "Heavy Rain",
    71: "Light Snow", 73: "Moderate Snow", 75: "Heavy Snow",
    80: "Rain Showers", 81: "Moderate Showers", 82: "Heavy Showers",
    95: "Thunderstorm", 96: "Thunderstorm with Hail",
    99: "Severe Thunderstorm",
}


def weather_description(code):
    return WEATHER_CODES.get(int(code or 0), "Unknown Weather")


def weather_icon(code):
    code = int(code or 0)
    if code == 0:
        return "☀️"
    if code in (1, 2, 3):
        return "⛅"
    if code in (45, 48):
        return "🌫️"
    if code in (51, 53, 55):
        return "🌦️"
    if code in (61, 63, 65, 80, 81, 82):
        return "🌧️"
    if code in (95, 96, 99):
        return "⛈️"
    return "🌤️"


def get_weather_data(city):
    """Get live Open-Meteo data with a cache + safe fallback.

    The dashboard must remain available even if an external weather
    service temporarily fails. The last successful response is reused
    first; otherwise a clearly labelled demo fallback is returned.
    """
    city = city if city in CITIES else "Nashik"
    lat, lon = CITIES[city]["lat"], CITIES[city]["lon"]

    # Per-process cache. It prevents a temporary upstream failure from
    # taking the dashboard down.
    cache = getattr(app, "weather_cache", {})
    if not hasattr(app, "weather_cache"):
        app.weather_cache = cache

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,relative_humidity_2m,"
        "apparent_temperature,precipitation,rain,weather_code,"
        "wind_speed_10m,surface_pressure"
        "&hourly=precipitation,precipitation_probability,rain,weather_code"
        "&forecast_days=2&timezone=auto"
    )

    try:
        r = requests.get(url, timeout=8)
        r.raise_for_status()
        data = r.json()

        if "current" not in data or "hourly" not in data:
            raise ValueError("Incomplete weather response")

        data["data_source"] = "LIVE"
        app.weather_cache[city] = data
        return data

    except Exception as exc:
        print(f"Weather API unavailable for {city}: {exc}")

        # Use the last good live response if one exists.
        if city in app.weather_cache:
            cached = dict(app.weather_cache[city])
            cached["data_source"] = "CACHED"
            return cached

        # No live data has ever been received: keep the site functional
        # with clearly labelled prototype values.
        now = datetime.now().replace(microsecond=0).isoformat()
        return {
            "data_source": "FALLBACK",
            "current": {
                "time": now,
                "temperature_2m": 27.0,
                "relative_humidity_2m": 78,
                "apparent_temperature": 29.0,
                "precipitation": 4.2,
                "rain": 4.2,
                "weather_code": 61,
                "wind_speed_10m": 12.0,
                "surface_pressure": 1008.0,
            },
            "hourly": {
                "time": [now, now, now],
                "precipitation": [4.2, 6.5, 8.1],
                "precipitation_probability": [75, 82, 88],
                "rain": [4.2, 6.5, 8.1],
                "weather_code": [61, 63, 65],
            },
        }

def get_next_hours(weather, count=3):
    hourly = weather.get("hourly", {})
    times = hourly.get("time", [])
    precipitation = hourly.get("precipitation", [])
    probability = hourly.get("precipitation_probability", [])
    codes = hourly.get("weather_code", [])

    current_time_string = weather.get("current", {}).get("time")
    start_index = 0

    if current_time_string:
        try:
            current_dt = datetime.fromisoformat(current_time_string)
            for i, value in enumerate(times):
                try:
                    if datetime.fromisoformat(value) >= current_dt:
                        start_index = i
                        break
                except ValueError:
                    continue
        except ValueError:
            pass

    result = []
    for hour in range(count):
        index = start_index + hour
        rain = float(precipitation[index]) if index < len(precipitation) else 0
        prob = int(probability[index]) if index < len(probability) else 0
        code = int(codes[index]) if index < len(codes) else 0

        result.append({
            "hour": f"+{hour + 1} Hour",
            "rain": round(rain, 2),
            "probability": prob,
            "weather_code": code,
            "icon": weather_icon(code),
        })
    return result


def calculate_flood_risk(current_rain, humidity, rain_1h, rain_2h, rain_3h):
    forecast_total = rain_1h + rain_2h + rain_3h

    current_score = min(max(current_rain, 0) * 15, 45)
    forecast_score = min(max(forecast_total, 0) * 6, 35)
    humidity_score = max(0, min((humidity - 60) * 0.35, 10))
    urban_score = 10

    score = min(round(
        current_score + forecast_score + humidity_score + urban_score
    ), 100)

    if score < 25:
        level, color, alert_color = "LOW", "#22c55e", "#16a34a"
    elif score < 50:
        level, color, alert_color = "MODERATE", "#eab308", "#ca8a04"
    elif score < 75:
        level, color, alert_color = "HIGH", "#f97316", "#ea580c"
    else:
        level, color, alert_color = "SEVERE", "#ef4444", "#dc2626"

    return score, level, color, alert_color


def calculate_drainage_status(risk_score, current_rain):
    load = min(round(risk_score * 0.75 + max(current_rain, 0) * 5), 100)

    if load < 40:
        status, color = "NORMAL", "#22c55e"
    elif load < 65:
        status, color = "HEAVY LOAD", "#eab308"
    elif load < 85:
        status, color = "OVERLOADED", "#f97316"
    else:
        status, color = "CRITICAL", "#ef4444"

    return load, status, color


def calculate_water_depth(risk_score, forecast_rain):
    return round(min(risk_score * 0.45 + forecast_rain * 2.5, 120), 1)


def calculate_flood_onset(risk_score):
    if risk_score < 25:
        return "No immediate flood threat"
    if risk_score < 50:
        return "Monitor next 3 hours"
    if risk_score < 75:
        return "Possible flooding in 1–3 hours"
    return "Immediate flood risk"


def generate_flood_zones(city, risk_score, water_depth):
    base = CITIES[city]
    names = [
        "Northern Sector", "Central Zone", "Low-Lying Area",
        "Commercial District", "Residential Area", "Southern Sector"
    ]
    offsets = [
        (0.025, 0.000), (0.010, 0.020), (-0.010, 0.015),
        (-0.020, -0.010), (0.000, -0.025), (-0.025, 0.005)
    ]

    zones = []
    for i, name in enumerate(names):
        variation = i * 8 - 18
        risk = max(5, min(100, risk_score + variation))
        depth = max(0, water_depth + variation * 0.4)

        if risk < 25:
            status, color = "SAFE", "#22c55e"
        elif risk < 50:
            status, color = "WATCH", "#eab308"
        elif risk < 75:
            status, color = "HIGH RISK", "#f97316"
        else:
            status, color = "FLOOD ALERT", "#ef4444"

        zones.append({
            "zone": name,
            "risk": round(risk),
            "depth": round(depth, 1),
            "status": status,
            "color": color,
            "lat": base["lat"] + offsets[i][0],
            "lon": base["lon"] + offsets[i][1],
            # Larger radius for high-risk zones.
            "radius_m": 350 + risk * 5
        })
    return zones


@app.route("/api/flood")
def flood_api():
    city = request.args.get("city", "Nashik")
    if city not in CITIES:
        city = "Nashik"

    weather = get_weather_data(city)

    current = weather.get("current", {})
    temperature = current.get("temperature_2m", 0)
    humidity = current.get("relative_humidity_2m", 0)
    rainfall = current.get("precipitation", 0)
    wind = current.get("wind_speed_10m", 0)
    pressure = current.get("surface_pressure", 0)
    feels_like = current.get("apparent_temperature", 0)
    weather_code = current.get("weather_code", 0)

    forecast = get_next_hours(weather)
    r1, r2, r3 = [x["rain"] for x in forecast]
    total = r1 + r2 + r3

    score, level, color, alert_color = calculate_flood_risk(
        rainfall, humidity, r1, r2, r3
    )
    drainage_load, drainage_status, drainage_color = calculate_drainage_status(
        score, rainfall
    )
    depth = calculate_water_depth(score, total)
    onset = calculate_flood_onset(score)
    zones = generate_flood_zones(city, score, depth)
    drainage_details = generate_drainage_details(city, score, rainfall)

    alerts = {
        "LOW": "Conditions are currently stable. No immediate flood threat detected.",
        "MODERATE": "Moderate flood possibility. Monitor rainfall and avoid low-lying roads.",
        "HIGH": "HIGH FLOOD ALERT: Heavy rainfall may cause urban waterlogging. Use safe-route guidance.",
        "SEVERE": "SEVERE FLOOD ALERT: Avoid low-lying areas and follow official emergency instructions.",
    }

    return jsonify({
        "success": True,
        "city": city,
        "data_source": weather.get("data_source", "LIVE"),
        "coordinates": CITIES[city],
        "updated_at": datetime.now().strftime("%d %B %Y | %I:%M:%S %p"),
        "weather": {
            "temperature": temperature,
            "feels_like": feels_like,
            "humidity": humidity,
            "rainfall": rainfall,
            "wind_speed": wind,
            "pressure": pressure,
            "weather_code": weather_code,
            "weather_description": weather_description(weather_code),
            "icon": weather_icon(weather_code),
        },
        "forecast": forecast,
        "flood": {
            "risk_score": score,
            "risk_level": level,
            "color": color,
            "estimated_water_depth_cm": depth,
            "flood_onset": onset,
        },
        "drainage": {
            "load_percentage": drainage_load,
            "status": drainage_status,
            "color": drainage_color,
            **drainage_details,
        },
        "alert": alerts[level],
        "alert_color": alert_color,
        "zones": zones,
    })



# ============================================================
# DETAILED URBAN DRAINAGE INTELLIGENCE
# ============================================================

def generate_drainage_details(city, risk_score, rainfall):
    """
    Prototype drainage intelligence.

    Replace these baseline values with municipal GIS/drainage-sensor
    data when such data is available.
    """
    profiles = {
        "Nashik": [
            ("Central Drainage Network", 82, 76, 14, 92, "HIGH"),
            ("Gangapur Road Sector", 68, 61, 10, 88, "MEDIUM"),
            ("Panchavati Sector", 91, 84, 22, 74, "HIGH"),
            ("CIDCO Sector", 73, 66, 12, 86, "MEDIUM"),
            ("Satpur Sector", 64, 55, 8, 90, "LOW"),
        ],
        "Mumbai": [
            ("Central Drainage Network", 95, 91, 28, 62, "HIGH"),
            ("Low-Lying Coastal Sector", 98, 94, 35, 55, "CRITICAL"),
            ("Western Urban Sector", 88, 82, 20, 70, "HIGH"),
            ("Eastern Urban Sector", 84, 77, 17, 76, "MEDIUM"),
            ("Residential Drain Network", 79, 71, 13, 82, "MEDIUM"),
        ],
        "Pune": [
            ("Central Drainage Network", 79, 72, 12, 89, "MEDIUM"),
            ("Kothrud Sector", 69, 62, 9, 91, "LOW"),
            ("Hadapsar Sector", 87, 80, 19, 78, "HIGH"),
            ("Shivajinagar Sector", 84, 77, 16, 81, "MEDIUM"),
            ("Low-Lying Road Network", 90, 84, 21, 73, "HIGH"),
        ],
        "Delhi": [
            ("Central Drainage Network", 77, 70, 11, 86, "MEDIUM"),
            ("Low-Lying Urban Sector", 89, 83, 20, 71, "HIGH"),
            ("Residential Drain Network", 72, 64, 10, 88, "LOW"),
            ("Commercial Sector", 82, 75, 15, 80, "MEDIUM"),
            ("Eastern Drainage Sector", 85, 79, 17, 76, "HIGH"),
        ],
        "Nagpur": [
            ("Central Drainage Network", 74, 66, 9, 90, "MEDIUM"),
            ("Low-Lying Sector", 86, 79, 18, 78, "HIGH"),
            ("Residential Network", 68, 59, 8, 92, "LOW"),
            ("Commercial Network", 78, 70, 13, 84, "MEDIUM"),
            ("Southern Drainage Sector", 81, 74, 14, 81, "MEDIUM"),
        ],
        "Chennai": [
            ("Central Drainage Network", 92, 87, 25, 68, "HIGH"),
            ("Coastal Low-Lying Sector", 97, 93, 31, 58, "CRITICAL"),
            ("Residential Drain Network", 84, 77, 16, 76, "HIGH"),
            ("Commercial Sector", 88, 82, 19, 72, "HIGH"),
            ("Southern Drainage Sector", 80, 73, 14, 83, "MEDIUM"),
        ],
        "Bengaluru": [
            ("Central Drainage Network", 76, 68, 10, 89, "MEDIUM"),
            ("Low-Lying Sector", 88, 81, 19, 74, "HIGH"),
            ("Residential Network", 71, 62, 9, 91, "LOW"),
            ("Commercial Sector", 80, 73, 14, 84, "MEDIUM"),
            ("Outer Drainage Sector", 83, 76, 16, 80, "MEDIUM"),
        ],
        "Hyderabad": [
            ("Central Drainage Network", 78, 70, 11, 88, "MEDIUM"),
            ("Low-Lying Sector", 90, 84, 21, 72, "HIGH"),
            ("Residential Network", 70, 61, 9, 92, "LOW"),
            ("Commercial Sector", 83, 76, 15, 82, "MEDIUM"),
            ("Southern Drainage Sector", 86, 79, 18, 77, "HIGH"),
        ],
    }

    rows = profiles.get(city, profiles["Nashik"])
    rainfall_factor = min(max(float(rainfall or 0) * 2, 0), 12)

    sections = []
    for name, capacity, load, blockage, efficiency, priority in rows:
        load = min(round(load + risk_score * 0.10 + rainfall_factor), 100)
        blockage = min(round(blockage + max(risk_score - 40, 0) * 0.08), 45)
        efficiency = max(
            45,
            round(efficiency - max(load - 70, 0) * 0.35)
        )

        if load >= 90 or blockage >= 30:
            status, color = "CRITICAL", "#ef4444"
        elif load >= 75 or blockage >= 20:
            status, color = "OVERLOADED", "#f97316"
        elif load >= 55 or blockage >= 10:
            status, color = "HEAVY LOAD", "#eab308"
        else:
            status, color = "NORMAL", "#22c55e"

        sections.append({
            "name": name,
            "capacity_percent": capacity,
            "load_percent": load,
            "blockage_percent": blockage,
            "efficiency_percent": efficiency,
            "status": status,
            "status_color": color,
            "maintenance_priority": priority,
        })

    avg_load = round(sum(x["load_percent"] for x in sections) / len(sections))
    avg_blockage = round(sum(x["blockage_percent"] for x in sections) / len(sections))
    avg_efficiency = round(sum(x["efficiency_percent"] for x in sections) / len(sections))
    critical = sum(x["status"] == "CRITICAL" for x in sections)
    overloaded = sum(x["status"] == "OVERLOADED" for x in sections)

    if critical:
        network_status, network_color = "CRITICAL", "#ef4444"
    elif overloaded:
        network_status, network_color = "OVERLOADED", "#f97316"
    elif avg_load >= 55:
        network_status, network_color = "HEAVY LOAD", "#eab308"
    else:
        network_status, network_color = "NORMAL", "#22c55e"

    return {
        "network_status": network_status,
        "network_color": network_color,
        "average_load_percent": avg_load,
        "average_blockage_percent": avg_blockage,
        "average_efficiency_percent": avg_efficiency,
        "critical_sections": critical,
        "overloaded_sections": overloaded,
        "sections": sections,
        "data_note": "Prototype/model-estimated drainage indicators. Replace with municipal GIS or sensor data for deployment.",
    }


# ============================================================
# FLOOD-AWARE ROUTING
# ============================================================

def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)

    a = (
        math.sin(dp / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


def point_to_zone_risk(lat, lon, zones):
    """
    Returns the strongest flood risk near a route point.
    Zones are model-predicted areas, not official sensor polygons.
    """
    strongest = 0
    for zone in zones:
        distance = haversine_m(lat, lon, zone["lat"], zone["lon"])
        radius = zone.get("radius_m", 500)

        if distance <= radius:
            # Risk decreases toward the edge of the zone.
            factor = max(0, 1 - distance / radius)
            local_risk = zone["risk"] * factor
            strongest = max(strongest, local_risk)
    return strongest


def score_route(route, zones):
    """
    Score an OSRM route using JALDRISHTI's modeled flood-risk zones.
    Lower score = safer route. This is decision-support, not official
    road closure or municipal flood-sensor data.
    """
    geometry = route.get("geometry", {})
    coordinates = geometry.get("coordinates", [])

    if not coordinates:
        return {
            "flood_exposure": 0,
            "high_risk_points": 0,
            "high_risk_percent": 0,
            "route_score": 0,
            "safety": "UNKNOWN",
        }

    # Sample up to 300 points along the route.
    step = max(1, len(coordinates) // 300)
    sampled_points = coordinates[::step]

    exposure = 0.0
    high_points = 0
    severe_points = 0

    for coord in sampled_points:
        lon, lat = coord[0], coord[1]
        local_risk = point_to_zone_risk(lat, lon, zones)
        exposure += local_risk

        if local_risk >= 50:
            high_points += 1
        if local_risk >= 75:
            severe_points += 1

    count = max(1, len(sampled_points))
    average_exposure = exposure / count
    high_risk_percent = (high_points / count) * 100
    severe_percent = (severe_points / count) * 100

    distance_km = route.get("distance", 0) / 1000

    # Flood exposure dominates route selection. Distance is only a small
    # tie-breaker so a slightly longer but much safer route can win.
    route_score = (
        average_exposure * 2.2
        + high_risk_percent * 1.8
        + severe_percent * 2.5
        + distance_km * 0.35
    )
    route_score = round(route_score, 2)

    if severe_percent >= 10 or average_exposure >= 55:
        safety = "DANGEROUS"
    elif high_risk_percent >= 15 or average_exposure >= 25:
        safety = "CAUTION"
    else:
        safety = "SAFER"

    return {
        "flood_exposure": round(average_exposure, 1),
        "high_risk_points": high_points,
        "high_risk_percent": round(high_risk_percent, 1),
        "severe_percent": round(severe_percent, 1),
        "route_score": route_score,
        "safety": safety,
    }


@app.route("/api/route")
def route_api():
    try:
        start_lat = float(request.args["start_lat"])
        start_lon = float(request.args["start_lon"])
        destination_lat = float(request.args["destination_lat"])
        destination_lon = float(request.args["destination_lon"])
    except (KeyError, ValueError):
        return jsonify({
            "success": False,
            "message": "Valid start and destination coordinates are required."
        }), 400

    city = request.args.get("city", "Nashik")
    if city not in CITIES:
        city = "Nashik"

    # Obtain the current JALDRISHTI flood model.
    weather = get_weather_data(city)

    current = weather.get("current", {})
    humidity = float(current.get("relative_humidity_2m", 0) or 0)
    rainfall = float(current.get("precipitation", 0) or 0)
    forecast = get_next_hours(weather)
    r1, r2, r3 = [x["rain"] for x in forecast]

    score, level, _, _ = calculate_flood_risk(
        rainfall, humidity, r1, r2, r3
    )
    depth = calculate_water_depth(score, r1 + r2 + r3)
    zones = generate_flood_zones(city, score, depth)

    # OSRM returns alternative driving routes where available.
    coordinates = (
        f"{start_lon},{start_lat};"
        f"{destination_lon},{destination_lat}"
    )
    url = (
        f"{OSRM_URL}/route/v1/driving/{coordinates}"
        "?alternatives=true&overview=full&geometries=geojson&steps=true"
    )

    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        routing = response.json()
    except requests.RequestException as exc:
        print("Routing API Error:", exc)
        return jsonify({
            "success": False,
            "message": "Routing service is temporarily unavailable."
        }), 503

    routes = routing.get("routes", [])
    if not routes:
        return jsonify({
            "success": False,
            "message": "No driving route could be found."
        }), 404

    scored = []
    for index, route in enumerate(routes):
        metrics = score_route(route, zones)
        scored.append({
            "index": index + 1,
            "route": route,
            "metrics": metrics
        })

    # Lowest flood-aware score wins. Distance is only a small tie-breaker.
    scored.sort(key=lambda x: (x["metrics"]["route_score"], x["route"].get("distance", 0)))
    selected = scored[0]
    selected_route = selected["route"]
    selected_metrics = selected["metrics"]

    # Build a compact route comparison for the dashboard.
    route_options = []
    for rank, item in enumerate(scored, start=1):
        route_options.append({
            "rank": rank,
            "distance_km": round(item["route"].get("distance", 0) / 1000, 2),
            "duration_min": round(item["route"].get("duration", 0) / 60),
            "flood_exposure": item["metrics"]["flood_exposure"],
            "high_risk_percent": item["metrics"]["high_risk_percent"],
            "route_score": item["metrics"]["route_score"],
            "safety": item["metrics"]["safety"],
            "selected": item is selected,
        })

    # If severe conditions exist, do not pretend any route is safe.
    emergency_warning = ""
    if level == "SEVERE":
        emergency_warning = (
            "Severe flood conditions are predicted. "
            "This route is a navigation suggestion only. "
            "Do not enter flooded roads and follow official emergency instructions."
        )
    elif selected_metrics["safety"] == "DANGEROUS":
        emergency_warning = (
            "The available route intersects model-predicted high-risk areas. "
            "Avoid flooded roads and consider delaying travel."
        )
    elif selected_metrics["safety"] == "CAUTION":
        emergency_warning = (
            "The selected route has some flood exposure. "
            "Drive carefully and avoid visible waterlogging."
        )
    else:
        emergency_warning = (
            "Selected route has the lowest modeled flood exposure "
            "among the available OSRM alternatives."
        )

    return jsonify({
        "success": True,
        "city": city,
        "data_source": weather.get("data_source", "LIVE"),
        "flood_risk": {
            "score": score,
            "level": level,
        },
        "route": {
            "distance_km": round(selected_route["distance"] / 1000, 2),
            "duration_min": round(selected_route["duration"] / 60),
            "geometry": selected_route["geometry"],
            "safety": selected_metrics["safety"],
            "flood_exposure": selected_metrics["flood_exposure"],
            "high_risk_points": selected_metrics["high_risk_points"],
            "high_risk_percent": selected_metrics["high_risk_percent"],
            "route_score": selected_metrics["route_score"],
        },
        "route_options": route_options,
        "alternatives_checked": len(scored),
        "warning": emergency_warning,
    })


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "JALDRISHTI"}), 200


# ============================================================
# FRONTEND
# ============================================================

HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>JALDRISHTI | Urban Flood Intelligence</title>

<link rel="stylesheet"
 href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">

<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>

<style>
*{margin:0;padding:0;box-sizing:border-box}

:root{
 --bg:#07111f;
 --card:rgba(17,32,51,.90);
 --cyan:#22d3ee;
 --blue:#38bdf8;
 --green:#22c55e;
 --yellow:#eab308;
 --orange:#f97316;
 --red:#ef4444;
 --text:#f8fafc;
 --muted:#94a3b8;
}

body{
 font-family:Arial,Helvetica,sans-serif;
 background:
 radial-gradient(circle at top left,#12385a,transparent 35%),
 linear-gradient(135deg,#030712,#071827,#0a2135);
 color:var(--text);
 min-height:100vh;
}

nav{
 position:sticky;top:0;z-index:1000;
 display:flex;align-items:center;justify-content:space-between;
 padding:16px 5%;
 background:rgba(3,7,18,.85);
 backdrop-filter:blur(18px);
 border-bottom:1px solid rgba(255,255,255,.08);
}

.logo{display:flex;align-items:center;gap:12px}
.logo-icon{
 width:48px;height:48px;display:grid;place-items:center;
 border-radius:14px;font-size:25px;
 background:linear-gradient(135deg,#0284c7,#06b6d4)
}
.logo h1{font-size:22px;letter-spacing:2px}
.logo p{color:var(--muted);font-size:12px;margin-top:3px}

.live-badge{
 display:flex;align-items:center;gap:8px;
 padding:9px 14px;border-radius:30px;
 background:rgba(34,197,94,.1);
 border:1px solid rgba(34,197,94,.4);
 color:#86efac;font-size:13px;
}
.live-dot{
 width:8px;height:8px;background:#22c55e;border-radius:50%;
 animation:pulse 1.5s infinite;
}
@keyframes pulse{
 0%{box-shadow:0 0 0 0 rgba(34,197,94,.7)}
 70%{box-shadow:0 0 0 10px rgba(34,197,94,0)}
 100%{box-shadow:0 0 0 0 rgba(34,197,94,0)}
}

.container{width:92%;max-width:1450px;margin:auto;padding:30px 0 70px}

.hero{
 display:grid;grid-template-columns:1.4fr 1fr;
 gap:20px;margin-bottom:20px;
}
.hero-main,.hero-side,.card,.stat-card{
 background:var(--card);
 border:1px solid rgba(255,255,255,.08);
 border-radius:22px;
}
.hero-main{
 padding:32px;
 background:linear-gradient(135deg,rgba(14,116,144,.3),rgba(15,23,42,.9));
}
.hero h2{font-size:clamp(30px,4vw,48px);line-height:1.08}
.hero h2 span{color:var(--cyan)}
.hero-text{margin-top:16px;color:var(--muted);line-height:1.7;max-width:700px}
.hero-side{padding:25px}
.controls{display:grid;gap:12px;margin-top:18px}

select,button{
 border:none;outline:none;padding:14px;border-radius:12px;font-size:15px;
}
select{background:#0f1f32;color:white;border:1px solid rgba(255,255,255,.12)}
button{cursor:pointer;font-weight:bold;color:white}
.primary-btn{background:linear-gradient(135deg,#0284c7,#06b6d4)}
.primary-btn:hover{transform:translateY(-2px)}
.location-btn{background:#17263a;border:1px solid rgba(255,255,255,.1)}

.stats-grid{
 display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:20px 0;
}
.stat-card{padding:20px}
.stat-top{display:flex;justify-content:space-between;color:var(--muted)}
.stat-icon{font-size:23px}
.stat-value{margin-top:13px;font-size:29px;font-weight:bold}
.stat-label{margin-top:5px;font-size:12px;color:var(--muted)}

.dashboard-grid{
 display:grid;grid-template-columns:1.1fr 1.9fr;gap:20px;margin-top:20px;
}
.card{padding:23px}
.card-title{
 display:flex;justify-content:space-between;align-items:center;
 margin-bottom:18px;
}
.card-title h3{font-size:18px}
.small-text{font-size:12px;color:var(--muted)}

.risk-circle-container{display:flex;justify-content:center;padding:10px}
.risk-circle{
 width:205px;height:205px;border-radius:50%;
 display:flex;flex-direction:column;justify-content:center;align-items:center;
 background:conic-gradient(#22c55e 0deg,#22c55e 20deg,#1e293b 20deg);
 position:relative;
}
.risk-circle:before{
 content:"";position:absolute;width:160px;height:160px;
 background:#0c1929;border-radius:50%;
}
.risk-score{position:relative;font-size:42px;font-weight:bold}
.risk-label{position:relative;color:var(--muted);margin-top:4px}
.risk-status{text-align:center;font-size:21px;font-weight:bold;margin:8px 0}

.alert-box{
 margin-top:17px;padding:16px;border-radius:14px;
 border-left:5px solid #22c55e;
 background:rgba(34,197,94,.08);
 line-height:1.55;
} .route-option{
  padding:14px 16px;margin-top:10px;border-radius:14px;
  background:rgba(15,31,50,.85);border:1px solid rgba(255,255,255,.08);
  display:grid;grid-template-columns:1.1fr repeat(4,1fr);gap:10px;align-items:center
 }
 .route-option.selected{border-color:rgba(34,211,238,.55);box-shadow:0 0 0 1px rgba(34,211,238,.08)}
 .route-pill{font-size:11px;font-weight:bold;padding:5px 8px;border-radius:999px;display:inline-block}
 @media(max-width:700px){.route-option{grid-template-columns:1fr 1fr}}


.forecast-grid{
 display:grid;grid-template-columns:repeat(3,1fr);gap:10px
}
.forecast-item{
 padding:17px;text-align:center;border-radius:15px;background:#0d1c2d
}
.forecast-icon{font-size:31px;margin:9px}
canvas{max-height:260px}

.route-panel{
 display:grid;grid-template-columns:1fr 1fr;gap:15px;margin-top:17px
}
.route-info{
 padding:17px;border-radius:14px;background:rgba(56,189,248,.06)
}
.route-value{
 font-size:25px;font-weight:bold;margin-top:7px;color:var(--cyan)
}

#map{
 width:100%;height:520px;border-radius:17px;overflow:hidden
}

.map-legend{
 display:flex;gap:15px;flex-wrap:wrap;margin-top:12px;
 font-size:12px;color:var(--muted)
}
.legend-dot{
 display:inline-block;width:11px;height:11px;border-radius:50%;margin-right:5px
}

.loading{text-align:center;padding:60px;color:var(--muted)}
.spinner{
 width:42px;height:42px;border:4px solid #1e293b;
 border-top:4px solid var(--cyan);border-radius:50%;margin:auto;
 animation:spin 1s linear infinite
}
@keyframes spin{to{transform:rotate(360deg)}}

.status-grid{
 display:grid;grid-template-columns:repeat(3,1fr);gap:15px
}

footer{text-align:center;color:var(--muted);padding:25px}

@media(max-width:1000px){
 .hero,.dashboard-grid{grid-template-columns:1fr}
 .stats-grid{grid-template-columns:repeat(2,1fr)}
}
@media(max-width:600px){
 nav{padding:13px}
 .live-badge{font-size:10px}
 .container{width:94%}
 .stats-grid,.forecast-grid,.route-panel,.status-grid{grid-template-columns:1fr}
 #map{height:430px}
}
</style>
</head>

<body>

<nav>
 <div class="logo">
  <div class="logo-icon">🌊</div>
  <div>
   <h1>JALDRISHTI</h1>
   <p>Urban Flood Intelligence System</p>
  </div>
 </div>
 <div class="live-badge"><div class="live-dot"></div> LIVE MONITORING</div>
</nav>

<div class="container">

<section class="hero">
 <div class="hero-main">
  <h2>Predict.<br><span>Protect.</span> Respond.</h2>
  <p class="hero-text">
   JALDRISHTI combines live weather intelligence, rainfall forecasting,
   urban flood-risk analytics and flood-aware route selection to support
   early warning and safer navigation.
  </p>
 </div>

 <div class="hero-side">
  <h3>📍 Monitoring Control</h3>
  <div class="controls">
   <select id="city">
    <option>Nashik</option>
    <option>Mumbai</option>
    <option>Pune</option>
    <option>Delhi</option>
    <option>Nagpur</option>
    <option>Chennai</option>
    <option>Bengaluru</option>
    <option>Hyderabad</option>
   </select>
   <button class="primary-btn" onclick="loadData()">🔄 Refresh Live Data</button>
   <button class="location-btn" onclick="getUserLocation()">📍 Use My Current Location</button>
  </div>
 </div>
</section>

<section class="stats-grid">
 <div class="stat-card">
  <div class="stat-top"><span>Temperature</span><span class="stat-icon">🌡️</span></div>
  <div class="stat-value" id="temperature">--</div>
  <div class="stat-label">Live Weather Data</div>
 </div>
 <div class="stat-card">
  <div class="stat-top"><span>Current Rainfall</span><span class="stat-icon">🌧️</span></div>
  <div class="stat-value" id="rainfall">--</div>
  <div class="stat-label">Current Precipitation</div>
 </div>
 <div class="stat-card">
  <div class="stat-top"><span>Humidity</span><span class="stat-icon">💧</span></div>
  <div class="stat-value" id="humidity">--</div>
  <div class="stat-label">Atmospheric Humidity</div>
 </div>
 <div class="stat-card">
  <div class="stat-top"><span>Wind Speed</span><span class="stat-icon">💨</span></div>
  <div class="stat-value" id="wind">--</div>
  <div class="stat-label">Current Wind Conditions</div>
 </div>
</section>

<section class="dashboard-grid">

 <div class="card">
  <div class="card-title">
   <h3>🚨 Flood Risk Intelligence</h3>
   <span class="small-text" id="weatherDescription">Loading...</span>
  </div>

  <div class="risk-circle-container">
   <div class="risk-circle" id="riskCircle">
    <div class="risk-score" id="riskScore">0</div>
    <div class="risk-label">RISK SCORE</div>
   </div>
  </div>

  <div class="risk-status" id="riskLevel">--</div>
  <div class="alert-box" id="alertBox">Loading live flood intelligence...</div>
 </div>

 <div class="card">
  <div class="card-title">
   <h3>⏳ Next 3 Hours Forecast</h3>
   <span class="small-text">LIVE FORECAST</span>
  </div>
  <div class="forecast-grid" id="forecastContainer"></div>
  <br>
  <canvas id="rainChart"></canvas>
 </div>

</section>

<section class="dashboard-grid">

 <div class="card">
  <div class="card-title"><h3>🌊 Flood Prediction</h3></div>

  <div style="display:grid;gap:15px">
   <div class="route-info">
    <div class="small-text">Estimated Water Depth</div>
    <div class="route-value" id="waterDepth">--</div>
   </div>

   <div class="route-info">
    <div class="small-text">Drainage System Load</div>
    <div class="route-value" id="drainageLoad">--</div>
    <div id="drainageStatus" class="small-text">--</div>
   </div>

   <div class="route-info">
    <div class="small-text">Flood Onset Prediction</div>
    <div style="margin-top:8px;font-weight:bold" id="floodOnset">--</div>
   </div>
  </div>
 </div>

 <div class="card">
  <div class="card-title">
   <h3>🗺️ Live Risk & Navigation Map</h3>
   <span class="small-text" id="mapCity">--</span>
  </div>
  <div id="map"></div>
  <div class="map-legend">
   <span><i class="legend-dot" style="background:#22c55e"></i>Safe</span>
   <span><i class="legend-dot" style="background:#eab308"></i>Watch</span>
   <span><i class="legend-dot" style="background:#f97316"></i>High Risk</span>
   <span><i class="legend-dot" style="background:#ef4444"></i>Flood Alert</span>
   <span>🔵 Selected Route</span>
  </div>
 </div>

</section>

<section class="card" style="margin-top:20px">
 <div class="card-title">
  <h3>🧭 Flood-Aware Safe Route Navigator</h3>
  <span class="small-text">GPS + OSRM ALTERNATIVE ROUTES + RISK SCORING</span>
 </div>

 <p class="small-text" style="line-height:1.6;margin-bottom:16px">
  The system compares available driving-route alternatives and selects the
  route with the lowest modeled flood exposure. This is a prototype
  decision-support feature, not an official emergency navigation service.
 </p>

 <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:15px">
  <button class="location-btn" onclick="getUserLocation()">📍 Get My Location</button>
  <button class="primary-btn" onclick="findSafeRoute()">🧭 Find Safer Route</button>
 </div>

 <div class="route-panel">
  <div class="route-info">
   <div class="small-text">Route Distance</div>
   <div class="route-value" id="routeDistance">--</div>
  </div>
  <div class="route-info">
   <div class="small-text">Estimated Travel Time</div>
   <div class="route-value" id="routeDuration">--</div>
  </div>
  <div class="route-info">
   <div class="small-text">Flood Exposure</div>
   <div class="route-value" id="routeExposure">--</div>
  </div>
  <div class="route-info">
   <div class="small-text">Route Safety</div>
   <div class="route-value" id="routeSafety">--</div>
  </div>
 </div>

 <div class="alert-box" id="routeMessage">
  📍 Waiting for your location...
 </div>

 <div id="routeComparison" style="margin-top:16px;display:none"></div>
</section>


<section class="card" style="margin-top:20px">
 <div class="card-title">
  <h3>🕳️ Urban Drainage Intelligence</h3>
  <span class="small-text">DRAINAGE NETWORK ANALYTICS</span>
 </div>

 <div class="status-grid">
  <div class="route-info">
   <div class="small-text">Network Status</div>
   <div class="route-value" id="drainageNetworkStatus">--</div>
  </div>
  <div class="route-info">
   <div class="small-text">Average Network Load</div>
   <div class="route-value" id="drainageAverageLoad">--</div>
  </div>
  <div class="route-info">
   <div class="small-text">Average Blockage</div>
   <div class="route-value" id="drainageBlockage">--</div>
  </div>
  <div class="route-info">
   <div class="small-text">Drainage Efficiency</div>
   <div class="route-value" id="drainageEfficiency">--</div>
  </div>
  <div class="route-info">
   <div class="small-text">Critical Sections</div>
   <div class="route-value" id="criticalSections">--</div>
  </div>
  <div class="route-info">
   <div class="small-text">Overloaded Sections</div>
   <div class="route-value" id="overloadedSections">--</div>
  </div>
 </div>

 <div style="overflow-x:auto;margin-top:18px">
  <table style="width:100%;border-collapse:collapse;min-width:760px">
   <thead>
    <tr style="text-align:left;color:#94a3b8;font-size:12px">
     <th style="padding:10px">Drainage Section</th>
     <th style="padding:10px">Capacity</th>
     <th style="padding:10px">Load</th>
     <th style="padding:10px">Blockage</th>
     <th style="padding:10px">Efficiency</th>
     <th style="padding:10px">Status</th>
     <th style="padding:10px">Priority</th>
    </tr>
   </thead>
   <tbody id="drainageTable"></tbody>
  </table>
 </div>

 <div class="alert-box" style="margin-top:18px" id="drainageNote">
  🕳️ Loading drainage intelligence...
 </div>
</section>

<section class="card" style="margin-top:20px">
 <div class="card-title"><h3>📡 System Status</h3></div>
 <div class="status-grid">
  <div class="route-info">
   <div class="small-text">Weather Data</div>
   <div style="margin-top:7px;font-weight:bold;color:#22d3ee">OPEN-METEO LIVE</div>
  </div>
  <div class="route-info">
   <div class="small-text">Routing Engine</div>
   <div style="margin-top:7px;font-weight:bold;color:#22d3ee">OSRM</div>
  </div>
  <div class="route-info">
   <div class="small-text">Last Updated</div>
   <div style="margin-top:7px;font-weight:bold" id="lastUpdated">--</div>
  </div>
 </div>
</section>

</div>

<footer>🌊 JALDRISHTI | Smart Urban Flood Nowcasting </footer>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>

<script>
let map;
let cityMarker = null;
let userMarker = null;
let routeLine = null;
let floodMarkers = [];
let userLocation = null;
let latestData = null;
let rainChart = null;

function initializeMap(){
 map = L.map("map").setView([19.9975,73.7898],12);

 L.tileLayer(
  "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
  {
   maxZoom:19,
   attribution:"© OpenStreetMap contributors"
  }
 ).addTo(map);
}

async function loadData(){
 const city = document.getElementById("city").value;

 try{
  const response = await fetch("/api/flood?city="+encodeURIComponent(city));
  const data = await response.json();

  if(!data.success){
   alert(data.message || "Unable to fetch data");
   return;
  }

  latestData = data;
  updateDashboard(data);
  updateMap(data);
 }catch(error){
  console.error(error);
  document.getElementById("alertBox").innerText =
   "❌ Unable to connect to live data service.";
 }
}

function updateDashboard(data){
 const w = data.weather;
 const f = data.flood;
 const d = data.drainage;

 document.getElementById("temperature").innerText = w.temperature+" °C";
 document.getElementById("rainfall").innerText = w.rainfall+" mm";
 document.getElementById("humidity").innerText = w.humidity+" %";
 document.getElementById("wind").innerText = w.wind_speed+" km/h";

 document.getElementById("weatherDescription").innerText =
  w.icon+" "+w.weather_description;

 document.getElementById("riskScore").innerText = f.risk_score;
 document.getElementById("riskLevel").innerText = f.risk_level+" FLOOD RISK";
 document.getElementById("riskLevel").style.color = f.color;

 const degrees = f.risk_score * 3.6;
 document.getElementById("riskCircle").style.background =
  `conic-gradient(${f.color} ${degrees}deg,#1e293b ${degrees}deg)`;

 const alertBox = document.getElementById("alertBox");
 alertBox.innerText = "⚠️ "+data.alert;
 alertBox.style.borderLeftColor = data.alert_color;

 document.getElementById("waterDepth").innerText =
  f.estimated_water_depth_cm+" cm";

 document.getElementById("drainageLoad").innerText =
  d.load_percentage+"%";

 document.getElementById("drainageStatus").innerText = d.status;
 document.getElementById("drainageStatus").style.color = d.color;

 document.getElementById("floodOnset").innerText = f.flood_onset;
 document.getElementById("lastUpdated").innerText = data.updated_at;
 document.getElementById("mapCity").innerText = "Monitoring "+data.city;

 updateForecast(data.forecast);
 updateRainChart(data.forecast);
 updateDrainageDetails(data.drainage);
}

function updateDrainageDetails(d){
 const status = document.getElementById("drainageNetworkStatus");
 status.innerText = d.network_status;
 status.style.color = d.network_color;

 document.getElementById("drainageAverageLoad").innerText =
  d.average_load_percent+"%";

 document.getElementById("drainageBlockage").innerText =
  d.average_blockage_percent+"%";

 document.getElementById("drainageEfficiency").innerText =
  d.average_efficiency_percent+"%";

 document.getElementById("criticalSections").innerText =
  d.critical_sections;

 document.getElementById("overloadedSections").innerText =
  d.overloaded_sections;

 const table = document.getElementById("drainageTable");
 table.innerHTML = "";

 d.sections.forEach(section=>{
  table.innerHTML += `
   <tr style="border-top:1px solid rgba(255,255,255,.07);font-size:13px">
    <td style="padding:11px">${section.name}</td>
    <td style="padding:11px">${section.capacity_percent}%</td>
    <td style="padding:11px">${section.load_percent}%</td>
    <td style="padding:11px">${section.blockage_percent}%</td>
    <td style="padding:11px">${section.efficiency_percent}%</td>
    <td style="padding:11px;font-weight:bold;color:${section.status_color}">
     ${section.status}
    </td>
    <td style="padding:11px">${section.maintenance_priority}</td>
   </tr>`;
 });

 document.getElementById("drainageNote").innerText =
  "🕳️ "+d.data_note;
}

function updateForecast(forecast){
 const container = document.getElementById("forecastContainer");
 container.innerHTML = "";

 forecast.forEach(item=>{
  container.innerHTML += `
   <div class="forecast-item">
    <div class="small-text">${item.hour}</div>
    <div class="forecast-icon">${item.icon}</div>
    <strong>${item.rain} mm</strong>
    <div class="small-text" style="margin-top:7px">
     ${item.probability}% Rain Probability
    </div>
   </div>`;
 });
}

function updateRainChart(forecast){
 const canvas = document.getElementById("rainChart");

 if(rainChart) rainChart.destroy();

 rainChart = new Chart(canvas,{
  type:"line",
  data:{
   labels:forecast.map(x=>x.hour),
   datasets:[{
    label:"Forecast Rainfall (mm)",
    data:forecast.map(x=>x.rain),
    borderColor:"#22d3ee",
    backgroundColor:"rgba(34,211,238,.12)",
    fill:true,
    tension:.4
   }]
  },
  options:{
   responsive:true,
   plugins:{
    legend:{labels:{color:"#cbd5e1"}}
   },
   scales:{
    x:{
     ticks:{color:"#94a3b8"},
     grid:{color:"rgba(255,255,255,.05)"}
    },
    y:{
     ticks:{color:"#94a3b8"},
     grid:{color:"rgba(255,255,255,.05)"},
     beginAtZero:true
    }
   }
  }
 });
}

function clearFloodMarkers(){
 floodMarkers.forEach(m=>map.removeLayer(m));
 floodMarkers=[];
}

function updateMap(data){
 const center=[data.coordinates.lat,data.coordinates.lon];

 map.setView(center,12);

 if(cityMarker) map.removeLayer(cityMarker);

 cityMarker=L.marker(center).addTo(map).bindPopup(`
  <b>🌊 JALDRISHTI</b><br>
  ${data.city}<br>
  Flood Risk: ${data.flood.risk_level}
 `);

 clearFloodMarkers();

 data.zones.forEach(zone=>{
  const circle=L.circle(
   [zone.lat,zone.lon],
   {
    radius:zone.radius_m,
    color:zone.color,
    fillColor:zone.color,
    fillOpacity:.18,
    weight:2
   }
  ).addTo(map);

  circle.bindPopup(`
   <b>${zone.zone}</b><br>
   Risk: ${zone.risk}/100<br>
   Estimated Depth: ${zone.depth} cm<br>
   Status: ${zone.status}
  `);

  floodMarkers.push(circle);
 });
}

function getUserLocation(){
 if(!navigator.geolocation){
  document.getElementById("routeMessage").innerText =
   "❌ Geolocation is not supported by this browser.";
  return;
 }

 document.getElementById("routeMessage").innerText =
  "📍 Getting your live location...";

 navigator.geolocation.getCurrentPosition(
  position=>{
   userLocation=[
    position.coords.latitude,
    position.coords.longitude
   ];

   if(userMarker) map.removeLayer(userMarker);

   userMarker=L.marker(userLocation)
    .addTo(map)
    .bindPopup("📍 Your Current Location")
    .openPopup();

   map.setView(userLocation,14);

   document.getElementById("routeMessage").innerText =
    "✅ Location detected. Click Find Safer Route.";
  },
  error=>{
   console.error(error);
   document.getElementById("routeMessage").innerText =
    "❌ Location permission denied or unavailable. Allow browser location access and try again.";
  },
  {
   enableHighAccuracy:true,
   timeout:10000,
   maximumAge:30000
  }
 );
}

async function findSafeRoute(){
 if(!userLocation){
  document.getElementById("routeMessage").innerText =
   "⚠️ Please get your current location first.";
  return;
 }

 if(!latestData){
  document.getElementById("routeMessage").innerText =
   "⚠️ Live flood data is still loading.";
  return;
 }

 document.getElementById("routeMessage").innerText =
  "🧭 Comparing available routes using flood-risk data...";

 const params=new URLSearchParams({
  start_lat:userLocation[0],
  start_lon:userLocation[1],
  destination_lat:latestData.coordinates.lat,
  destination_lon:latestData.coordinates.lon,
  city:latestData.city
 });

 try{
  const response=await fetch("/api/route?"+params.toString());
  const data=await response.json();

  if(!data.success){
   throw new Error(data.message || "Route not found");
  }

  const coords=data.route.geometry.coordinates.map(c=>[c[1],c[0]]);

  if(routeLine) map.removeLayer(routeLine);

  routeLine=L.polyline(coords,{
   color:"#22d3ee",
   weight:7,
   opacity:.95
  }).addTo(map);

  map.fitBounds(routeLine.getBounds(),{padding:[40,40]});

  document.getElementById("routeDistance").innerText =
   data.route.distance_km+" km";

  document.getElementById("routeDuration").innerText =
   data.route.duration_min+" min";

  document.getElementById("routeExposure").innerText =
   data.route.flood_exposure;

  const safety=document.getElementById("routeSafety");
  safety.innerText=data.route.safety;

  if(data.route.safety==="SAFER"){
   safety.style.color="#22c55e";
  }else if(data.route.safety==="CAUTION"){
   safety.style.color="#eab308";
  }else{
   safety.style.color="#ef4444";
  }

  document.getElementById("routeMessage").innerText =
   "🧭 "+data.warning+
   ` Checked ${data.alternatives_checked} available route alternative(s).`;

  renderRouteComparison(data.route_options || []);

 }catch(error){
  console.error(error);
  document.getElementById("routeMessage").innerText =
   "❌ Unable to calculate a flood-aware route. "+error.message;
 }
}

function renderRouteComparison(options){
 const box=document.getElementById("routeComparison");
 if(!options.length){
  box.style.display="none";
  return;
 }

 box.style.display="block";
 let html=`<div class="small-text" style="margin-bottom:8px">ROUTE COMPARISON — LOWER SCORE = LOWER MODELED FLOOD EXPOSURE</div>`;

 options.forEach((r)=>{
  const safeColor=r.safety==="SAFER" ? "#22c55e" : (r.safety==="CAUTION" ? "#eab308" : "#ef4444");
  const selected=r.selected ? " selected" : "";
  const badge=r.selected ? "SELECTED SAFER ROUTE" : "ALTERNATIVE";
  html+=`
   <div class="route-option${selected}">
    <div><strong>Route ${r.rank}</strong><br><span class="route-pill" style="color:${safeColor};background:${safeColor}22">${badge}</span></div>
    <div><div class="small-text">Distance</div><strong>${r.distance_km} km</strong></div>
    <div><div class="small-text">Time</div><strong>${r.duration_min} min</strong></div>
    <div><div class="small-text">Flood Exposure</div><strong>${r.flood_exposure}</strong></div>
    <div><div class="small-text">Risk</div><strong style="color:${safeColor}">${r.safety}</strong></div>
   </div>`;
 });
 box.innerHTML=html;
}

document.getElementById("city").addEventListener("change",()=>{
 if(routeLine){
  map.removeLayer(routeLine);
  routeLine=null;
 }
 document.getElementById("routeDistance").innerText="--";
 document.getElementById("routeDuration").innerText="--";
 document.getElementById("routeExposure").innerText="--";
 document.getElementById("routeSafety").innerText="--";
 document.getElementById("routeComparison").style.display="none";
 loadData();
});

setInterval(loadData,60000);

initializeMap();
loadData();
</script>

</body>
</html>
"""


@app.route("/")
def home():
    return render_template_string(HTML)


if __name__ == "__main__":
    app.run(
        debug=True,
        host="0.0.0.0",
        port=5000
    )
