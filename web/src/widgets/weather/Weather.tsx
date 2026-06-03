import { type FormEvent, useCallback, useEffect, useState } from 'react';

/**
 * Weather — the Stage-2 glance canary. Pure external read (Open-Meteo, no API
 * key, CORS-friendly): geocode a city → current conditions. Proves the widget
 * runtime (registry + dock + pop-out) end-to-end with zero agent coupling.
 */

interface Wx {
  tempF: number;
  code: number;
  windMph: number;
  place: string;
}

// WMO weather-code → label + glyph (coarse buckets are enough for a glance).
function wmo(code: number): { label: string; emoji: string } {
  if (code === 0) return { label: 'Clear', emoji: '☀️' };
  if (code <= 2) return { label: 'Partly cloudy', emoji: '⛅' };
  if (code === 3) return { label: 'Overcast', emoji: '☁️' };
  if (code <= 48) return { label: 'Fog', emoji: '🌫️' };
  if (code <= 57) return { label: 'Drizzle', emoji: '🌦️' };
  if (code <= 67) return { label: 'Rain', emoji: '🌧️' };
  if (code <= 77) return { label: 'Snow', emoji: '🌨️' };
  if (code <= 82) return { label: 'Showers', emoji: '🌦️' };
  if (code <= 86) return { label: 'Snow showers', emoji: '🌨️' };
  return { label: 'Thunderstorm', emoji: '⛈️' };
}

export function Weather() {
  const [city, setCity] = useState('San Francisco');
  const [query, setQuery] = useState('San Francisco');
  const [wx, setWx] = useState<Wx | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchWx = useCallback(async (name: string) => {
    setLoading(true);
    setError(null);
    try {
      const g = await fetch(
        `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(name)}&count=1&language=en&format=json`,
      );
      const gj = await g.json();
      const loc = gj?.results?.[0];
      if (!loc) throw new Error(`No match for "${name}"`);
      const place = [loc.name, loc.admin1, loc.country_code].filter(Boolean).join(', ');
      const w = await fetch(
        `https://api.open-meteo.com/v1/forecast?latitude=${loc.latitude}&longitude=${loc.longitude}` +
          `&current=temperature_2m,weather_code,wind_speed_10m&temperature_unit=fahrenheit&wind_speed_unit=mph`,
      );
      const wj = await w.json();
      const c = wj?.current;
      if (!c) throw new Error('No weather data');
      setWx({
        tempF: Math.round(c.temperature_2m),
        code: c.weather_code,
        windMph: Math.round(c.wind_speed_10m),
        place,
      });
    } catch (e) {
      setError((e as Error).message);
      setWx(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchWx(query);
  }, [query, fetchWx]);

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const c = city.trim();
    if (c) setQuery(c);
  };

  const cond = wx ? wmo(wx.code) : null;

  return (
    <div className="space-y-2.5">
      <form onSubmit={onSubmit} className="flex gap-1.5">
        <input
          value={city}
          onChange={(e) => setCity(e.target.value)}
          placeholder="City"
          spellCheck={false}
          className="flex-1 h-8 rounded-md border border-edge bg-raised/60 px-2 text-xs text-fg-body placeholder-fg-muted"
        />
        <button
          type="submit"
          className="h-8 px-2.5 rounded-md border border-edge text-xs text-fg-muted hover:text-fg-body hover:bg-edge transition-colors"
        >
          Go
        </button>
      </form>

      {loading && <div className="text-xs text-fg-subtle">Loading…</div>}
      {error && <div className="text-xs text-danger break-words">{error}</div>}
      {wx && cond && !loading && (
        <div className="flex items-center gap-3">
          <div className="text-3xl leading-none" aria-hidden>
            {cond.emoji}
          </div>
          <div className="min-w-0">
            <div className="text-2xl font-semibold text-fg leading-tight">{wx.tempF}°F</div>
            <div className="text-xs text-fg-muted truncate">
              {cond.label} · {wx.windMph} mph
            </div>
            <div className="text-helper text-fg-subtle truncate">{wx.place}</div>
          </div>
        </div>
      )}
    </div>
  );
}
