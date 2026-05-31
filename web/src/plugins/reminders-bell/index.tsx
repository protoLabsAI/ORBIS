import { registerPlugin } from '../PluginHost';
import { RemindersBell } from './RemindersBell';

// Top-level reminders affordance — a bell in the top-right chrome with an
// orb-tinted dot when reminders are scheduled. Lifted out of the Agent
// settings tab so reminders are reachable without opening settings.
registerPlugin({
  id: 'reminders-bell',
  slots: {
    'overlay-top': RemindersBell,
  },
});
