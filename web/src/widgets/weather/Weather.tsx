import { useEffect, useState } from 'react';
import {
  Cloud,
  CloudDrizzle,
  CloudFog,
  CloudLightning,
  CloudRain,
  CloudSnow,
  CloudSun,
  type LucideIcon,
  Sun,
} from 'lucide-react';

/**
 * Weather — the Stage-2 glance canary, redesigned minimal/voice-first: an
 * ambient readout, no inputs. Location defaults for now; setting it by voice
 * ("what's the weather in Tokyo") arrives with the render_widget tool (2c).
 * Pure external read (Open-Meteo, no API key, CORS-friendly).
 */

const DEFAULT_CITY = 'San Francisco';

function wmoIcon(code: number): LucideIcon {
  if (code === 0) return Sun;
  if (code <= 2) return CloudSun;
  if (code === 3) return Cloud;
  if (code <= 48) return CloudFog;
  if (code <= 57) return CloudDrizzle;
  if (code <= 67) return CloudRain;
  if (code <= 77) return CloudSnow;
  if (code <= 82) return CloudRain;
  if (code <= 86) return CloudSnow;
  return CloudLightning;
}

function wmoLabel(code: number): string {
  if (code === 0) return 'Clear';
  if (code <= 2) return 'Partly cloudy';
  if (code === 3) return 'Overcast';
  if (code <= 48) return 'Fog';
  if (code <= 57) return 'Drizzle';
  if (code <= 67) return 'Rain';
  if (code <= 77) return 'Snow';
  if (code <= 82) return 'Showers';
  if (code <= 86) return 'Snow showers';
  return 'Thunderstorm';
}

interface Wx {
  tempF: number;
  code: number;
  windMph: number;
  city: string;
}

export function Weather() {
  const [wx, setWx] = useState<Wx | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const g = await fetch(
          `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(DEFAULT_CITY)}&count=1&language=en&format=json`,
        );
        const loc = (await g.json())?.results?.[0];
        if (!loc) throw new Error('geo');
        const w = await fetch(
          `https://api.open-meteo.com/v1/forecast?latitude=${loc.latitude}&longitude=${loc.longitude}` +
            `&current=temperature_2m,weather_code,wind_speed_10m&temperature_unit=fahrenheit&wind_speed_unit=mph`,
        );
        const c = (await w.json())?.current;
        if (!c) throw new Error('wx');
        if (alive) {
          setWx({
            tempF: Math.round(c.temperature_2m),
            code: c.weather_code,
            windMph: Math.round(c.wind_speed_10m),
            city: loc.name,
          });
        }
      } catch {
        if (alive) setError(true);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (error) return <div className="text-helper text-fg-faint">weather unavailable</div>;
  if (!wx) return <div className="text-helper text-fg-faint">…</div>;

  const Icon = wmoIcon(wx.code);
  return (
    <div className="flex items-center gap-2.5 pr-1">
      <Icon className="h-6 w-6 shrink-0 text-fg-subtle" strokeWidth={1.25} />
      <div className="min-w-0 leading-tight">
        <div className="text-xl font-light tabular-nums text-fg-body">{wx.tempF}°</div>
        <div className="text-helper text-fg-subtle truncate">
          {wmoLabel(wx.code)} · {wx.windMph} mph · {wx.city}
        </div>
      </div>
    </div>
  );
}
