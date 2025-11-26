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
        },
        assets: {
            logo: '/dt-icon.png',
        },
        text: {
            title: '5G Slice Order Portal',
            loginTitle: '5G Slice Portal',
        }
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
        },
        assets: {
            logo: '/vodafone-icon.png',
        },
        text: {
            title: 'Vodafone 5G Portal',
            loginTitle: 'Vodafone 5G Portal',
        }
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
        },
        assets: {
            logo: '/o2-icon.png',
        },
        text: {
            title: 'O2 5G Portal',
            loginTitle: 'O2 5G Portal',
        }
    }
};

export const getTheme = () => {
    const skin = process.env.REACT_APP_SKIN || 'tmobile';
    return themes[skin] || themes.tmobile;
};
