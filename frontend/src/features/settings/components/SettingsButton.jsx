import { useEffect, useRef, useState } from 'react';
import { useSettings } from '../SettingsContext';
import { useEscapeKey } from '../../../shared/hooks/useEscapeKey';
import styles from './SettingsButton.module.css';

const TOGGLES = [
    { key: 'disableSpeaking', label: 'Disable speaking questions' },
    { key: 'disableListening', label: 'Disable listening questions' },
];

// Gear button pinned to the top-right corner; opens a small settings panel.
export function SettingsButton() {
    const { settings, updateSetting } = useSettings();
    const [open, setOpen] = useState(false);
    const rootRef = useRef(null);

    useEscapeKey(() => setOpen(false), open);

    // close when clicking anywhere outside the button/panel
    useEffect(() => {
        if (!open) return undefined;
        const onPointerDown = (e) => {
            if (!rootRef.current?.contains(e.target)) setOpen(false);
        };
        document.addEventListener('pointerdown', onPointerDown);
        return () => document.removeEventListener('pointerdown', onPointerDown);
    }, [open]);

    return (
        <div ref={rootRef} className={styles.root}>
            <button
                type="button"
                className={styles.trigger}
                aria-label="Settings"
                aria-expanded={open}
                onClick={() => setOpen(o => !o)}
            >
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <circle cx="12" cy="12" r="3" />
                    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
                </svg>
            </button>

            {open && (
                <div className={styles.panel} role="dialog" aria-label="Settings">
                    <h3 className={styles.title}>Settings</h3>
                    {TOGGLES.map(({ key, label }) => (
                        <label key={key} className={styles.row}>
                            <span>{label}</span>
                            <input
                                type="checkbox"
                                role="switch"
                                className={styles.switch}
                                checked={settings[key]}
                                onChange={(e) => updateSetting(key, e.target.checked)}
                            />
                        </label>
                    ))}
                </div>
            )}
        </div>
    );
}
