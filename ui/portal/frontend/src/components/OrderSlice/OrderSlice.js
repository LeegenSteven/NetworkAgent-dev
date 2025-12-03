import React, { useState } from 'react';
import './OrderSlice.css';
import Dialog from '../Dialog/Dialog';
import MapSelection from '../MapSelection/MapSelection';



const OrderSlice = ({ onNavigate, theme }) => {
  const cities = theme.cities || {};
  const defaultCity = Object.keys(cities)[0];

  const [sliceType, setSliceType] = useState('eMBB');
  const [bandwidth, setBandwidth] = useState(250);
  const [geographicArea, setGeographicArea] = useState(defaultCity);
  const [duration, setDuration] = useState('1 day, week');
  const [mapCenter, setMapCenter] = useState(cities[defaultCity]);
  const [coverageArea, setCoverageArea] = useState(null);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [dialogConfig, setDialogConfig] = useState({ title: '', message: '', type: 'success' });
  const [isOrdering, setIsOrdering] = useState(false);

  const estimatedPrice = 450;

  const handleAreaChange = (e) => {
    const city = e.target.value;
    setGeographicArea(city);
    setMapCenter(cities[city]);
  };

  const handlePlaceOrder = () => {
    setIsOrdering(true);
    const orderDetails = {
      sliceType,
      bandwidth,
      geographicArea,
      duration,
      coverageArea
    };

    fetch('/order', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(orderDetails),
    })
      .then((response) => response.json())
      .then((data) => {
        if (data.success) {
          setDialogConfig({
            title: 'Order Successful',
            message: 'Your 5G Network Slice has been successfully ordered. You will be notified once it is provisioned.',
            type: 'success'
          });
          setDialogOpen(true);
        } else {
          throw new Error(data.message || 'Order failed');
        }
      })
      .catch((error) => {
        console.error('Error placing order:', error);
        setDialogConfig({
          title: 'Order Failed',
          message: 'There was an issue placing your order. Please try again later.',
          type: 'error'
        });
        setDialogOpen(true);
      })
      .finally(() => {
        setIsOrdering(false);
      });
  };

  const handleDialogClose = () => {
    setDialogOpen(false);
    if (dialogConfig.type === 'success' && onNavigate) {
      onNavigate('slices');
    }
  };

  return (
    <div className="order-slice-container">
      <Dialog
        isOpen={dialogOpen}
        onClose={handleDialogClose}
        title={dialogConfig.title}
        message={dialogConfig.message}
        type={dialogConfig.type}
      />
      <div className="configure-section">
        <h2>Configure Your 5G Slice</h2>

        <div className="form-group">
          <label>Slice Type:</label>
          <select value={sliceType} onChange={(e) => setSliceType(e.target.value)} className="form-select">
            <option value="eMBB">eMBB</option>
            <option value="URLLC">URLLC</option>
            <option value="mMTC">mMTC</option>
          </select>
        </div>

        <div className="form-group">
          <label>Bandwidth (Mbps):</label>
          <div className="bandwidth-control">
            <input
              type="range"
              min="10"
              max="1000"
              value={bandwidth}
              onChange={(e) => setBandwidth(e.target.value)}
              className="bandwidth-slider"
            />
            <div className="bandwidth-values">
              <span>{bandwidth}</span>
              <span>10 - 1.0k</span>
            </div>
          </div>
        </div>

        <div className="form-group">
          <label>Geographic Area</label>
          <select value={geographicArea} onChange={handleAreaChange} className="form-select">
            {Object.keys(cities).map((city) => (
              <option key={city} value={city}>{city}</option>
            ))}
          </select>
          <MapSelection center={mapCenter} onAreaSelected={setCoverageArea} />
          {coverageArea && (
            <div className="coordinates-display">
              <h4>Selected Area Coordinates:</h4>
              <div className="coordinates-list">
                {coverageArea.map((coord, index) => (
                  <div key={index} className="coordinate-item">
                    <span className="coord-label">Point {index + 1}:</span>
                    <span>{coord.lat.toFixed(6)}, {coord.lng.toFixed(6)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="form-group">
          <label>Duration</label>
          <select value={duration} onChange={(e) => setDuration(e.target.value)} className="form-select">
            <option value="1 hour">1 hour</option>
            <option value="1 day, week">1 day, week</option>
            <option value="1 month">1 month</option>
          </select>
          <div className="duration-buttons">
            <button className="duration-btn" onClick={() => setDuration('1 hour')}>1 hour</button>
            <button className="duration-btn" onClick={() => setDuration('1 month')}>1 month</button>
          </div>
        </div>

        <button
          className="place-order-btn"
          onClick={handlePlaceOrder}
          disabled={isOrdering}
        >
          {isOrdering ? <div className="spinner"></div> : 'Place Order'}
        </button>
      </div>

      <div className="order-summary">
        <h2>Order Summary</h2>
        <div className="summary-item">
          <span>Slice Type:</span>
          <span>URLLC</span>
        </div>
        <div className="summary-item">
          <span>Bandwidth:</span>
          <span>250 Mbps</span>
        </div>
        <div className="summary-item">
          <span>Area:</span>
          <span>{geographicArea}</span>
        </div>
        <div className="summary-item">
          <span>Duration:</span>
          <span>1 day</span>
        </div>
        <div className="estimated-price">
          <span>Estimated Price:</span>
          <span className="price">{theme.currency || '€'}{estimatedPrice}</span>
        </div>
      </div>
    </div>
  );
};

export default OrderSlice;
