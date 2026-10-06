import { createContext, useContext, useEffect, useState } from 'react';

const STORAGE_KEY = 'app-settings';

const DEFAULT_SETTINGS = {
    disableSpeaking: false,
    disableListening: false,
};

const loadSettings = () => {
    try {
        return { ...DEFAULT_SETTINGS, ...JSON.parse(localStorage.getItem(STORAGE_KEY)) };
    } catch {
        return DEFAULT_SETTINGS;
    }
};

const SettingsContext = createContext(null);

// Holds user settings app-wide and persists them to localStorage.
export function SettingsProvider({ children }) {
    const [settings, setSettings] = useState(loadSettings);

    useEffect(() => {
        try { localStorage.setItem(STORAGE_KEY, JSON.stringify(settings)); } catch { /* storage unavailable */ }
    }, [settings]);

    const updateSetting = (key, value) => setSettings(prev => ({ ...prev, [key]: value }));

    return (
        <SettingsContext.Provider value={{ settings, updateSetting }}>
            {children}
        </SettingsContext.Provider>
    );
}

export function useSettings() {
    const ctx = useContext(SettingsContext);
    if (!ctx) throw new Error('useSettings must be used inside <SettingsProvider>');
    return ctx;
}
