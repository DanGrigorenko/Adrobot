from fastapi import FastAPI, HTTPException


def create_app(keitaro):
    app = FastAPI()

    @app.get('/api/campaigns')
    def campaigns(q: str = ''):
        query = q.strip().casefold()
        try:
            results = keitaro.campaigns()
        except ConnectionError:
            raise HTTPException(status_code=502, detail='Keitaro недоступен') from None
        return [
            campaign for campaign in results
            if not query or query in campaign['name'].casefold()
            or query == str(campaign['id'])
        ]

    return app
