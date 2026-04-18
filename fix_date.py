import joblib

for m in ['short', 'mid', 'long']:
    path = f'/Users/nahyeonho/AlphaModels/global_{m}_model.joblib'
    data = joblib.load(path)
    features = data['features']
    if 'Date' in features:
        features.remove('Date')
        data['features'] = features
        joblib.dump(data, path)
        print(f"Fixed Date in {m}")
