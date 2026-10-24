import { useSettings } from '../SettingsContext';
import styles from './DisabledQuestionsBanner.module.css';

// Always-visible notice while any question type is turned off in settings,
// so a shorter session isn't mistaken for a bug.
export function DisabledQuestionsBanner() {
    const { settings } = useSettings();

    const disabled = [
        settings.disableSpeaking && 'speaking',
        settings.disableListening && 'listening',
    ].filter(Boolean);

    if (disabled.length === 0) return null;

    return (
        <div className={styles.banner} role="status">
            {disabled.join(' and ')} questions are turned off in settings, so you'll see fewer questions.
        </div>
    );
}
