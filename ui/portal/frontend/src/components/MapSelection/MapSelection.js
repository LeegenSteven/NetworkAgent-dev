import React, { useState, useCallback, useEffect } from 'react';
import { GoogleMap, useJsApiLoader, Polygon } from '@react-google-maps/api';
import './MapSelection.css';

const containerStyle = {
    width: '100%',
    height: '400px'
};

const defaultCenter = {
    lat: 51.0459, // Leverkusen
    lng: 6.9867
};

// Move libraries outside component to prevent recreation
const libraries = ['drawing'];

const MapSelection = ({ center, onAreaSelected }) => {
    const { isLoaded } = useJsApiLoader({
        id: 'google-map-script',
        googleMapsApiKey: process.env.REACT_APP_GOOGLE_MAPS_API_KEY,
        libraries: libraries
    });

    const [map, setMap] = useState(null);
    const [isDrawing, setIsDrawing] = useState(false);
    const [polygonPath, setPolygonPath] = useState([]);

    const onLoad = useCallback(function callback(map) {
        setMap(map);
    }, []);

    const onUnmount = useCallback(function callback(map) {
        setMap(null);
    }, []);

    const handleMapClick = useCallback((e) => {
        console.log('Map clicked, isDrawing:', isDrawing, 'event:', e);
        if (!isDrawing) return;

        const newPoint = {
            lat: e.latLng.lat(),
            lng: e.latLng.lng()
        };

        console.log('Adding point:', newPoint);
        setPolygonPath(prev => [...prev, newPoint]);
    }, [isDrawing]);

    const startDrawing = () => {
        console.log('Starting drawing mode');
        setIsDrawing(true);
        setPolygonPath([]);
    };

    const completePolygon = () => {
        console.log('Completing polygon with', polygonPath.length, 'points');
        if (polygonPath.length < 3) {
            alert('Please click at least 3 points to create a polygon');
            return;
        }

        setIsDrawing(false);

        if (onAreaSelected) {
            onAreaSelected(polygonPath);
        }
    };

    const clearPolygon = () => {
        console.log('Clearing polygon');
        setPolygonPath([]);
        setIsDrawing(false);
        if (onAreaSelected) {
            onAreaSelected(null);
        }
    };

    // Update map center when prop changes
    useEffect(() => {
        if (map && center) {
            map.panTo(center);
        }
    }, [map, center]);

    if (!isLoaded) {
        return <div>Loading Map...</div>;
    }

    return (
        <div className="map-selection-container">
            <div className="map-controls">
                <button
                    className={`map-control-icon ${isDrawing ? 'active' : ''}`}
                    onClick={() => {
                        if (isDrawing) {
                            // Cancel drawing
                            setIsDrawing(false);
                            setPolygonPath([]);
                        } else if (polygonPath.length > 0) {
                            // Clear existing polygon
                            clearPolygon();
                        } else {
                            // Start drawing
                            startDrawing();
                        }
                    }}
                    title={isDrawing ? 'Click to cancel' : polygonPath.length > 0 ? 'Clear area' : 'Draw area'}
                >
                    {isDrawing ? '✕' : polygonPath.length > 0 ? '🗑️' : '✏️'}
                </button>
                {isDrawing && polygonPath.length >= 3 && (
                    <button
                        className="map-control-icon complete-icon"
                        onClick={completePolygon}
                        title="Complete polygon"
                    >
                        ✓
                    </button>
                )}
            </div>
            <GoogleMap
                mapContainerStyle={containerStyle}
                center={center || defaultCenter}
                zoom={12}
                onLoad={onLoad}
                onUnmount={onUnmount}
                onClick={handleMapClick}
                options={{
                    draggableCursor: isDrawing ? 'crosshair' : 'default',
                    draggingCursor: isDrawing ? 'crosshair' : 'grab'
                }}
            >
                {polygonPath.length > 0 && (
                    <Polygon
                        paths={polygonPath}
                        options={{
                            fillColor: '#2196F3',
                            fillOpacity: 0.4,
                            strokeColor: '#2196F3',
                            strokeWeight: 2,
                            clickable: false,
                            editable: false,
                            zIndex: 1,
                        }}
                    />
                )}
            </GoogleMap>
        </div>
    );
};

export default React.memo(MapSelection);
