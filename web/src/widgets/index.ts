import { CloudSun } from 'lucide-react';
import { registerPlugin } from '../plugins/PluginHost';
import { registerWidget } from './registry';
import { WidgetLauncher } from './WidgetLauncher';
import { Weather } from './weather/Weather';

// Register the built-in widgets. A new widget is one more registerWidget(...).
registerWidget({
  id: 'weather',
  title: 'Weather',
  icon: CloudSun,
  klass: 'glance',
  render: Weather,
});

// The launcher is chrome (always-on rail button), so it rides the plugin slot.
registerPlugin({ id: 'widget-launcher', slots: { 'overlay-top': WidgetLauncher } });
