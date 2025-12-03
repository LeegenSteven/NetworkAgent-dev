export const themes = {
    tmobile: {
        name: 'tmobile',
        colors: {
            primary: '#e20074',
            primaryHover: '#b0005b',
            background: '#f5f5f5',
            text: '#262626',
            textLight: '#666',
            white: '#ffffff',
            lightOverlay: 'rgba(255, 255, 255, 0.1)',
            primaryLight: 'rgba(226, 0, 116, 0.1)',
            border: '#e20074',
            headerBackground: '#e20074',
            button: '#e20074',
            slider: '#e20074',
        },
        assets: {
            loginLogo: '/dt-icon.png',
            headerLogo: '/dt-icon.png',
        },
        text: {
            title: '5G Slice Order Portal',
            loginTitle: '5G Slice Order Portal',
        },
        cities: {
            'Leverkusen': { lat: 51.0459, lng: 6.9867 },
            'Berlin': { lat: 52.5200, lng: 13.4050 },
            'Munich': { lat: 48.1351, lng: 11.5820 },
            'Hamburg': { lat: 53.5511, lng: 9.9937 }
        },
        currency: '€'
    },
    vodafone: {
        name: 'vodafone',
        colors: {
            primary: '#e60000',
            primaryHover: '#b30000',
            background: '#f4f4f4',
            text: '#333333',
            textLight: '#666666',
            white: '#ffffff',
            lightOverlay: 'rgba(255, 255, 255, 0.15)',
            primaryLight: 'rgba(230, 0, 0, 0.1)',
            border: '#e60000',
            headerBackground: '#e60000',
            button: '#e60000',
            slider: '#e60000',
        },
        assets: {
            loginLogo: '/vodafone-icon.png',
            headerLogo: '/vodafone-icon.png',
        },
        text: {
            title: '5G Slice Order Portal',
            loginTitle: '5G Slice Order Portal',
        },
        cities: {
            'Leverkusen': { lat: 51.0459, lng: 6.9867 },
            'Berlin': { lat: 52.5200, lng: 13.4050 },
            'Munich': { lat: 48.1351, lng: 11.5820 },
            'Hamburg': { lat: 53.5511, lng: 9.9937 }
        },
        currency: '€'
    },
    o2: {
        name: 'o2',
        colors: {
            primary: '#032b5a',
            primaryHover: '#0019a5',
            background: '#f8f9fa',
            text: '#032b5a',
            textLight: '#5c6c7f',
            white: '#ffffff',
            lightOverlay: 'rgba(255, 255, 255, 0.15)',
            primaryLight: 'rgba(3, 43, 90, 0.1)',
            border: '#032b5a',
            headerBackground: 'linear-gradient(90deg, #0019a5 0%, #032b5a 100%)',
            button: '#032b5a',
            slider: '#032b5a',
        },
        assets: {
            loginLogo: '/vmo2-logo.svg',
            headerLogo: '/o2-logo.svg',
        },
        text: {
            title: '5G Slice Order Portal',
            loginTitle: '5G Slice Order Portal',
        },
        cities: {
            'London': { lat: 51.5074, lng: -0.1278 },
            'Manchester': { lat: 53.4808, lng: -2.2426 },
            'Birmingham': { lat: 52.4862, lng: -1.8904 },
            'Glasgow': { lat: 55.8642, lng: -4.2518 }
        },
        currency: '£'
    }
};

export const getTheme = () => {
    const skin = process.env.REACT_APP_SKIN || 'tmobile';
    return themes[skin] || themes.tmobile;
};
